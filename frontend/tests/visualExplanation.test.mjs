import assert from "node:assert/strict";
import test from "node:test";

import { shouldAutoplay, wrapFrame } from "../src/visualExplanation.mjs";

test("wrapFrame cycles in both directions", () => {
  assert.equal(wrapFrame(4, 4), 0);
  assert.equal(wrapFrame(-1, 4), 3);
  assert.equal(wrapFrame(2, 4), 2);
  assert.equal(wrapFrame(5, 0), 0);
});

test("animated explanations autoplay unless reduced motion is requested", () => {
  assert.equal(shouldAutoplay("animated", 4, false), true);
  assert.equal(shouldAutoplay("animated", 4, true), false);
  assert.equal(shouldAutoplay("diagram", 4, false), false);
  assert.equal(shouldAutoplay("animated", 1, false), false);
});
