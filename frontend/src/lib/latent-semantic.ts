export interface LatentModel { idf: Record<string, number>; vectors: Record<string, number>[]; axes: number[][]; eigenvalues: number[] }

const stop = new Set('the and for with from this that your you are was were have has will can not our och att det den som med för från till ett en är har på av vi ni de'.split(' '));
const words = (value: string) => (value.toLocaleLowerCase().match(/[\p{L}\p{N}]{2,}/gu) || []).filter(word => !stop.has(word)).slice(0, 20000);
const dot = (a: number[], b: number[]) => a.reduce((sum, value, index) => sum + value * b[index], 0);
const unit = (a: number[]) => { const length = Math.sqrt(dot(a, a)); return a.map(value => length > 1e-12 ? value / length : 0); };

/** A real corpus-derived low-rank LSA projection; no query/provider I/O. */
export function latentModel(items: Array<{ title: string; text: string }>): LatentModel {
  const counts = items.map(item => { const result: Record<string, number> = Object.create(null); for (const word of words(item.title + '\n' + item.text)) result[word] = (result[word] || 0) + 1; return result; });
  const df: Record<string, number> = Object.create(null);
  for (const row of counts) for (const term of Object.keys(row)) df[term] = (df[term] || 0) + 1;
  const idf: Record<string, number> = Object.create(null);
  for (const term of Object.keys(df).sort((a, b) => df[b] - df[a] || a.localeCompare(b)).slice(0, 4096)) idf[term] = Math.log((items.length + 1) / (df[term] + 1)) + 1;
  const vectors = counts.map(row => { const vector: Record<string, number> = Object.create(null); let squared = 0; for (const term of Object.keys(row)) if (idf[term]) { vector[term] = (1 + Math.log(row[term])) * idf[term]; squared += vector[term] ** 2; } const length = Math.sqrt(squared) || 1; for (const term of Object.keys(vector)) vector[term] /= length; return vector; });
  function multiply(values: number[]) { const terms: Record<string, number> = Object.create(null); vectors.forEach((row, index) => { for (const term of Object.keys(row)) terms[term] = (terms[term] || 0) + row[term] * values[index]; }); return vectors.map(row => Object.keys(row).reduce((sum, term) => sum + row[term] * terms[term], 0)); }
  const axes: number[][] = [], eigenvalues: number[] = [];
  for (let k = 0; k < Math.min(12, Math.max(1, Math.floor(items.length / 2))); k++) {
    let vector = unit(items.map((_, index) => Math.sin((index + 1) * (k + 1) * 1.618) + .1));
    for (let iteration = 0; iteration < 24; iteration++) { let projected = multiply(vector); for (const axis of axes) { const amount = dot(projected, axis); projected = projected.map((value, index) => value - amount * axis[index]); } vector = unit(projected); }
    const eigenvalue = dot(vector, multiply(vector)); if (eigenvalue < 1e-8) break; axes.push(vector); eigenvalues.push(eigenvalue);
  }
  return { idf, vectors, axes, eigenvalues };
}

export function rankSemantic<T extends { title: string; text: string; id: string; kind: string }>(items: T[], model: LatentModel, query: string): Array<T & { score: number }> {
  const counts: Record<string, number> = Object.create(null); for (const term of words(query)) counts[term] = (counts[term] || 0) + 1;
  const vector: Record<string, number> = Object.create(null); let squared = 0;
  for (const term of Object.keys(counts)) if (model.idf[term]) { vector[term] = (1 + Math.log(counts[term])) * model.idf[term]; squared += vector[term] ** 2; }
  const length = Math.sqrt(squared) || 1; for (const term of Object.keys(vector)) vector[term] /= length;
  const affinities = model.vectors.map(row => Object.keys(vector).reduce((sum, term) => sum + vector[term] * (row[term] || 0), 0));
  const querySpace = unit(model.axes.map((axis, index) => dot(axis, affinities) / Math.sqrt(model.eigenvalues[index])));
  return items.map((item, index) => { const space = unit(model.axes.map((axis, n) => axis[index] * Math.sqrt(model.eigenvalues[n]))); return { ...item, score: .8 * Math.max(0, dot(querySpace, space)) + .2 * affinities[index] }; }).filter(row => row.score > .05).sort((a, b) => b.score - a.score || a.kind.localeCompare(b.kind) || a.id.localeCompare(b.id));
}
