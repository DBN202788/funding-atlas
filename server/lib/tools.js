// The advisor's tools. Each one only reads published data, so nothing an application
// says can make the advisor change anything. These mirror TOOLS in web/index.html.
const STATES = ["FL", "DE", "LA"];
export const OUTCOMES = ["Funded", "Appropriated", "Appropriated (name match)", "Possible match, check source", "No appropriation found by name", "Funded (veto check uncertain)", "In enrolled budget (veto check pending)", "Vetoed by Governor", "No budget citation found", "Requested (budget not yet checked)"];
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
  get_requirements: { type: "object", properties: { state: { type: "string", enum: STATES } }, required: ["state"] },
};

const DESCRIPTIONS = {
  search_funding: "Search the funding records by keywords. Returns up to 15 matching records with id, name, state, year, requested, budgeted, vetoed, status, program area and sponsor, plus the total number of matches.",
  summarize_funding: "Return totals for a state and fiscal year, grouped by program area: number of records, number in a budget or act, number vetoed, dollars requested and dollars budgeted.",
  get_application: "Return what an application says, by record id, for Florida FY2026-27 and Louisiana 2026: organization, type, budget, description or summary, goals, and outcome. Louisiana records also list the act lines that name the organization.",
  compare_applications: "Return how often features such as matching funds, a stated number served, and measurable outcomes appear in funded, vetoed and uncited Florida FY2026-27 applications, with sample sizes.",
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

const IMPLEMENTATIONS = {
  async search_funding(input, store) {
    const { records } = await store.records();
    const terms = (input.query || "").toLowerCase().split(/\s+/).filter(Boolean);
    const rows = records.filter(r => (!input.state || r.state === input.state) && (!input.fiscal_year || r.year === input.fiscal_year)
      && (!input.outcome || r.status === input.outcome) && terms.every(t => r.hay.includes(t)))
      .sort((a, b) => (b.appropriated || 0) - (a.appropriated || 0));
    return {
      total_matches: rows.length,
      records: rows.slice(0, 15).map(r => ({ id: r.id, name: r.name, state: r.state, year: r.year, requested: r.requested, budgeted: r.appropriated, vetoed: r.vetoed, status: r.status, area: r.category, sponsor: r.sponsor })),
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
