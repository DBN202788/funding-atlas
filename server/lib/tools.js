// The advisor's tools. Each one only reads published data, so nothing an application
// says can make the advisor change anything. These mirror TOOLS in web/index.html.
const STATES = ["FL", "DE", "LA", "MD", "UT"];
export const OUTCOMES = ["Funded", "Appropriated", "Appropriated (name match)", "Possible match, check source", "No appropriation found by name", "Funded (veto check uncertain)", "In enrolled budget (veto check pending)", "Vetoed by Governor", "No budget citation found", "Requested (budget not yet checked)", "No funding listed", "Withdrawn"];
const IN_BUDGET = new Set(["Funded", "Appropriated", "Appropriated (name match)", "Funded (veto check uncertain)", "In enrolled budget (veto check pending)"]);
// A single very long application shouldn't be able to use up a question's budget.
const MAX_RESULT_CHARS = 12000;

const SCHEMAS = {
  search_funding: {
    type: "object",
    properties: {
      query: { type: "string", description: "Keywords; every word must match" },
      state: { type: "string", enum: STATES },
      fiscal_year: { type: "string", description: "Such as FY2026-27 for Florida, FY2027 for Delaware, or 2026 Regular Session for Louisiana" },
      outcome: { type: "string", enum: OUTCOMES },
    },
  },
  summarize_funding: {
    type: "object",
    properties: { state: { type: "string", enum: STATES }, fiscal_year: { type: "string" } },
    required: ["state"],
  },
  get_application: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
  compare_applications: { type: "object", properties: { scope: { type: "string", enum: ["nonprofit", "all"] } } },
  comparable_outcomes: {
    type: "object",
    properties: { query: { type: "string", description: "One to three short keywords; every word must match" }, state: { type: "string", enum: STATES } },
    required: ["query", "state"],
  },
  get_veto_details: { type: "object", properties: { id: { type: "string" } }, required: ["id"] },
  get_requirements: { type: "object", properties: { state: { type: "string", enum: STATES } }, required: ["state"] },
};

const DESCRIPTIONS = {
  search_funding: "Search the funding records by keywords. Leave out state to search every state. Returns up to 15 matching records with id, name, state, year, requested, budgeted, vetoed, status, program area and sponsor, shared across states when no state is given, plus the total number of matches and by_state counts.",
  summarize_funding: "Return totals for a state and fiscal year, grouped by program area: number of records, number in a budget or act, number vetoed, dollars requested and dollars budgeted.",
  get_application: "Return what an application says, by record id, for Florida FY2026-27 and Louisiana 2026: organization, type, budget, description or summary, goals, and outcome. Louisiana records also list the act lines that name the organization.",
  compare_applications: "Return how often features such as matching funds, a stated number served, and measurable outcomes appear in funded, vetoed and uncited Florida FY2026-27 applications, with sample sizes.",
  comparable_outcomes: "Show how similar past requests fared, using the most recent year with checked outcomes for FL, LA or MD, awards only for DE, or requests only for UT. Returns counts, percentages when there are at least 10 matches, amount ranges, examples and the limits of the data. It describes the past and doesn't predict funding.",
  get_veto_details: "Return what the official record says about a vetoed item, by record id: the vetoed amount, a link to the Governor's letter, what that letter says, and how to ask for a reason.",
  get_requirements: "Return the application checklist, key dates, cautions and official links for FL, DE or LA.",
};

export const TOOLS = Object.keys(SCHEMAS).map(name => ({
  name,
  description: DESCRIPTIONS[name],
  input_schema: SCHEMAS[name],
  eager_input_streaming: true,
}));

/** Streamed tool input isn't checked by the API, so check it here before use. */
export function inputProblem(name, input) {
  const schema = SCHEMAS[name];
  if (!schema) return `Unknown tool ${name}.`;
  if (!input || typeof input !== "object" || Array.isArray(input)) return "The tool input must be an object.";
  for (const key of schema.required || []) if (typeof input[key] !== "string" || !input[key]) return `${key} is required.`;
  for (const [key, value] of Object.entries(input)) {
    const rule = schema.properties[key];
    if (!rule) return `Unknown field ${key}.`;
    if (typeof value !== "string") return `${key} must be text.`;
    if (rule.enum && !rule.enum.includes(value)) return `${key} must be one of: ${rule.enum.join(", ")}.`;
  }
  return null;
}

const OUTCOME_SCOPE = {
  FL: { year: "FY2026-27", rates: true, limits: "Only FY2026-27 has been checked for vetoes." },
  LA: { year: "2026 Regular Session", rates: true, limits: "Louisiana outcomes come from matching organization names, so an act's money may not be for the requested project." },
  DE: { year: "FY2027", rates: false, limits: "Delaware publishes awards only, so unsuccessful requests can't be counted." },
  MD: { year: "2026 Regular Session", rates: true, limits: "Maryland's list covers capital projects only." },
  UT: { year: "2026 General Session", rates: false, limits: "Utah's list doesn't show whether a request was funded, so no outcome can be counted." },
};
const median = values => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b), middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
};

function stateCounts(rows) {
  const counts = {};
  for (const state of STATES) {
    const here = rows.filter(r => r.state === state), funded = here.filter(r => IN_BUDGET.has(r.status));
    counts[state] = { matches: here.length, in_budget: funded.length, vetoed: here.filter(r => r.status === "Vetoed by Governor").length, budgeted: funded.reduce((sum, r) => sum + (r.appropriated || 0), 0) };
  }
  return counts;
}

/** Takes rows in turn from each state, so a state with larger awards can't crowd out the others. */
function shareAcrossStates(rows, limit) {
  const queues = STATES.map(state => rows.filter(r => r.state === state)), picked = [];
  while (picked.length < limit && queues.some(queue => queue.length)) {
    for (const queue of queues) if (queue.length && picked.length < limit) picked.push(queue.shift());
  }
  return picked;
}

const IMPLEMENTATIONS = {
  async search_funding(input, store) {
    const { records } = await store.records();
    const terms = (input.query || "").toLowerCase().split(/\s+/).filter(Boolean);
    const rows = records.filter(r => (!input.state || r.state === input.state) && (!input.fiscal_year || r.year === input.fiscal_year)
      && (!input.outcome || r.status === input.outcome) && terms.every(t => r.hay.includes(t)))
      .sort((a, b) => (b.appropriated || 0) - (a.appropriated || 0));
    return {
      total_matches: rows.length,
      by_state: stateCounts(rows),
      records: (input.state ? rows.slice(0, 15) : shareAcrossStates(rows, 15)).map(r => ({ id: r.id, name: r.name, state: r.state, year: r.year, requested: r.requested, budgeted: r.appropriated, vetoed: r.vetoed, status: r.status, area: r.category, sponsor: r.sponsor, ...(r.vetoUrl && (r.vetoed || r.status === "Vetoed by Governor") ? { veto_letter: r.vetoUrl } : {}) })),
    };
  },
  async summarize_funding(input, store) {
    const { records } = await store.records();
    const rows = records.filter(r => r.state === input.state && (!input.fiscal_year || r.year === input.fiscal_year));
    const areas = new Map();
    for (const r of rows) {
      const area = areas.get(r.category) || { area: r.category, records: 0, in_budget: 0, vetoed: 0, requested: 0, budgeted: 0 };
      area.records += 1;
      area.requested += r.requested || 0;
      if (IN_BUDGET.has(r.status)) { area.in_budget += 1; area.budgeted += r.appropriated || 0; }
      if (r.status === "Vetoed by Governor") area.vetoed += 1;
      areas.set(r.category, area);
    }
    return [...areas.values()];
  },
  async get_application(input, store) {
    const { byId } = await store.records();
    const r = byId.get(input.id);
    if (!r) throw new Error("No record has that id.");
    const meta = await store.meta();
    const isLouisiana = r.state === "LA";
    if (!isLouisiana && (r.state !== "FL" || !(meta.form_years || []).includes(r.year))) throw new Error("This record's form hasn't been read.");
    const forms = await store.forms(isLouisiana ? `LA-${r.id.split("-")[1]}` : r.year);
    const form = forms[r.form];
    if (!form) throw new Error("This form hasn't been read yet.");
    return { ...form, status: r.status };
  },
  async compare_applications(input, store) {
    const all = await store.compare();
    const picked = all.find(c => c.forms_compared >= 100) || all[0];
    if (!picked) throw new Error("No comparison is available yet.");
    const scope = picked[input.scope === "all" ? "all" : "nonprofit"];
    return { fiscal_year: picked.fiscal_year, forms_read: picked.forms_read, counts: scope.counts, features: scope.features, by_org_type: picked.by_org_type };
  },
  async comparable_outcomes(input, store) {
    const { records } = await store.records();
    const scope = OUTCOME_SCOPE[input.state];
    const terms = input.query.toLowerCase().split(/\s+/).filter(Boolean);
    const rows = terms.length ? records.filter(r => r.state === input.state && r.year === scope.year && terms.every(t => r.hay.includes(t))) : [];
    const funded = rows.filter(r => IN_BUDGET.has(r.status)), vetoed = rows.filter(r => r.status === "Vetoed by Governor");
    const amounts = funded.map(r => r.appropriated).filter(n => n != null), asked = rows.map(r => r.requested).filter(n => n != null);
    const share = n => scope.rates && rows.length >= 10 ? Math.round(n / rows.length * 100) : null;
    return {
      state: input.state, year: scope.year, matches: rows.length, in_budget: funded.length, vetoed: vetoed.length, other: rows.length - funded.length - vetoed.length,
      in_budget_percent: share(funded.length), vetoed_percent: share(vetoed.length),
      median_requested: median(asked), median_budgeted: median(amounts), lowest_budgeted: amounts.length ? Math.min(...amounts) : null, highest_budgeted: amounts.length ? Math.max(...amounts) : null,
      examples: [...rows].sort((a, b) => (b.appropriated || 0) - (a.appropriated || 0)).slice(0, 5).map(r => ({ id: r.id, name: r.name, status: r.status, requested: r.requested, budgeted: r.appropriated })),
      limits: `${scope.limits} This matches words, so some records may not be truly similar. It shows what happened, not why, and doesn't predict funding.`,
    };
  },
  async get_veto_details(input, store) {
    const { byId } = await store.records();
    const r = byId.get(input.id);
    if (!r) throw new Error("No record has that id.");
    if (r.status !== "Vetoed by Governor" && !r.vetoed) return { id: r.id, name: r.name, status: r.status, vetoed: false };
    const notes = await store.vetoNotes(), note = notes[r.state]?.[r.year];
    return {
      id: r.id, name: r.name, status: r.status, vetoed: true, vetoed_amount: r.vetoed, letter_url: r.vetoUrl || null, letter_date: note?.letter_date || null,
      reason_given_for_this_item: false, what_the_letter_says: note?.says || "The veto letter for this year hasn't been reviewed for reasons, so read it directly.", how_to_ask: notes.how_to_ask,
    };
  },
  async get_requirements(input, store) {
    const requirements = await store.requirements();
    if (!requirements[input.state]) throw new Error("Only FL, DE and LA have requirements loaded.");
    return requirements[input.state];
  },
};

/** Runs one tool and returns the block to send back to the model. */
export async function runTool(block, store) {
  const problem = inputProblem(block.name, block.input);
  if (problem) return { type: "tool_result", tool_use_id: block.id, is_error: true, content: problem };
  try {
    const text = JSON.stringify(await IMPLEMENTATIONS[block.name](block.input, store));
    const content = text.length > MAX_RESULT_CHARS ? `${text.slice(0, MAX_RESULT_CHARS)}\n[This result was cut short at ${MAX_RESULT_CHARS} characters. Say so if it matters to your answer.]` : text;
    return { type: "tool_result", tool_use_id: block.id, content };
  } catch (error) {
    return { type: "tool_result", tool_use_id: block.id, is_error: true, content: error.message };
  }
}
