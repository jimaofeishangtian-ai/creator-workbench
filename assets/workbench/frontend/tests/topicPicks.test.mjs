import assert from "node:assert/strict";
import { test } from "node:test";
import { pickTopics } from "../src/topicPicks.ts";

test("the shortlist skips hashtag-only cues, used topics and duplicate angles without padding", () => {
  const rows = [
    { id: "1", title: "你看这个机器人！ #AI教程 #实操" },
    { id: "2", title: "知识库搭建教程", topic_key: "搭知识库" },
    { id: "3", title: "手把手搭建知识库", topic_key: "搭知识库" },
    { id: "4", title: "实测两个方案", state: "已选题" },
    { id: "5", title: "即梦分镜的镜头设计", content_tracks: ["AIGC"] },
  ];
  assert.deepEqual(pickTopics(rows).map(pick => pick.video.id), ["2", "5"]);
});
