import { test } from "node:test";
import assert from "node:assert/strict";
import { readProjectDraft, writeProjectDraft } from "../src/projectDraft.ts";

test("reopening stale local edits preserves text and detects another device's newer revision", () => {
  const local = { content: "我的原稿", brief: { must_include: "甲方要求" } };
  const restored = readProjectDraft(writeProjectDraft(local, 4), 5);
  assert.deepEqual(restored.fields, local);
  assert.equal(restored.baseRevision, 4);
  assert.equal(restored.conflict, true);
});
test("same-revision drafts remain editable without a false conflict", () => {
  const result = readProjectDraft(writeProjectDraft({ content: "未保存修改" }, 4), 4);
  assert.equal(result.conflict, false);
  assert.equal(result.fields.content, "未保存修改");
});
test("legacy drafts survive but require review because their original revision is unknown", () => {
  const result = readProjectDraft('{"content":"旧设备草稿"}', 7);
  assert.equal(result.fields.content, "旧设备草稿");
  assert.equal(result.conflict, true);
});
test("missing or malformed storage cannot break opening a project", () => {
  for (const raw of [null, "{", "[]", "null", '{"format":1,"fields":[],"baseRevision":2}']) {
    assert.deepEqual(readProjectDraft(raw, 4), { fields: null, baseRevision: 4, conflict: false });
  }
});
