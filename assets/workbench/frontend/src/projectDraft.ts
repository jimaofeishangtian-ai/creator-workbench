type Row = Record<string, any>;

// Keep a draft tied to the revision it was actually edited from, including after closing the editor.
export function readProjectDraft(raw: string | null, revision: number): { fields: Row | null; baseRevision: number; conflict: boolean } {
  if (!raw) return { fields: null, baseRevision: revision, conflict: false };
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid draft");
    if (value.format === 1) {
      if (!value.fields || typeof value.fields !== "object" || Array.isArray(value.fields) || !Number.isInteger(value.baseRevision) || value.baseRevision < 1) throw new Error("Invalid draft");
      return { fields: value.fields, baseRevision: value.baseRevision, conflict: value.baseRevision !== revision };
    }
    // Older drafts have no reliable base version. Preserve them for review; never silently overwrite a newer server draft.
    return { fields: value, baseRevision: revision, conflict: true };
  } catch {
    return { fields: null, baseRevision: revision, conflict: false };
  }
}

export function writeProjectDraft(fields: Row, baseRevision: number): string {
  return JSON.stringify({ format: 1, baseRevision, fields });
}
