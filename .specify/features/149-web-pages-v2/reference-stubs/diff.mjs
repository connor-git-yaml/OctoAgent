export function diffLines(before, after) {
  if (before === after) return [{ value: String(after ?? "") }];
  return [
    { value: String(before ?? ""), removed: true },
    { value: String(after ?? ""), added: true },
  ];
}
