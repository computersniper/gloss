import assert from "node:assert/strict";
import test from "node:test";

import {
  collectTags,
  filterPapersByTag,
  mergeTags,
  removeTag,
  tagsMatch,
} from "../src/libraryTags.mjs";

const papers = [
  { id: "rl", tags: ["Reinforcement Learning", "Robotics"] },
  { id: "nlp", tags: ["NLP", "reinforcement learning"] },
  { id: "none", tags: [] },
];

test("direction tags are collected case-insensitively", () => {
  assert.deepEqual(collectTags(papers), ["NLP", "Reinforcement Learning", "Robotics"]);
});

test("papers can be filtered by a direction tag regardless of case", () => {
  assert.deepEqual(filterPapersByTag(papers, "REINFORCEMENT LEARNING").map((paper) => paper.id), ["rl", "nlp"]);
  assert.equal(filterPapersByTag(papers, null).length, 3);
  assert.equal(tagsMatch("Reinforcement Learning", "reinforcement learning"), true);
  assert.equal(tagsMatch("NLP", "Robotics"), false);
});

test("tag editing handles Chinese commas, whitespace, duplicates, and removal", () => {
  const tags = mergeTags(["NLP"], "  Robotics，nlp, Computer   Vision ");
  assert.deepEqual(tags, ["NLP", "Robotics", "Computer Vision"]);
  assert.deepEqual(removeTag(tags, "robotics"), ["NLP", "Computer Vision"]);
});
