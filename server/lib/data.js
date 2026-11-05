// Reads the same published data files as the website, so the two can't disagree.
export class DataUnavailable extends Error {}

export function decodeRecords(payload) {
  const d = payload.dict;
  return payload.rows.map(r => {
    const rec = {
      state: d.state[r[0]], year: d.year[r[2]], name: r[3], group: r[4] < 0 ? "" : d.group[r[4]], category: d.category[r[5]],
      sponsor: r[7] < 0 ? "" : d.sponsor[r[7]], requested: r[8], appropriated: r[9], vetoed: r[10], status: d.status[r[11]], form: r[12], id: r[18], organization: r[19] || "",
    };
    rec.hay = `${rec.name} ${rec.organization} ${rec.group} ${rec.sponsor} ${rec.category} ${rec.form} ${r[20] || ""}`.toLowerCase();
    return rec;
  });
}

export function createDataStore({ baseUrl, fetchImpl = fetch, ttlMs = 3600_000 }) {
  const cache = new Map();
  const load = (name, parse = x => x) => {
    const hit = cache.get(name);
    if (hit && Date.now() - hit.at < ttlMs) return hit.value;
    const value = fetchImpl(`${baseUrl}/${name}`).then(async response => {
      if (!response.ok) throw new DataUnavailable(`${name}: HTTP ${response.status}`);
      return parse(await response.json());
    }).catch(error => {
      cache.delete(name);
      throw error instanceof DataUnavailable ? error : new DataUnavailable(`${name}: ${error.message}`);
    });
    cache.set(name, { at: Date.now(), value });
    return value;
  };
  return {
    records: () => load("records.json", payload => {
      const records = decodeRecords(payload);
      return { records, byId: new Map(records.map(r => [r.id, r])) };
    }),
    meta: () => load("meta.json"),
    forms: key => load(`forms_${key}.json`),
    compare: () => load("compare.json"),
    requirements: () => load("requirements.json"),
  };
}
