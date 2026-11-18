import assert from "node:assert/strict";
import test from "node:test";

import { buildLinkedInJobViewUrl, getOpenJobUrl } from "./jobLinks.js";

test("buildLinkedInJobViewUrl returns a canonical LinkedIn job page URL", () => {
  assert.equal(
    buildLinkedInJobViewUrl("101"),
    "https://www.linkedin.com/jobs/view/101/"
  );
});

test("getOpenJobUrl prefers the LinkedIn job page over the apply link", () => {
  assert.equal(
    getOpenJobUrl({
      linkedin_job_id: "101",
      apply_link:
        "https://www.linkedin.com/safety/go/?url=https%3A%2F%2Fexample.com%2Fapply",
    }),
    "https://www.linkedin.com/jobs/view/101/"
  );
});

test("getOpenJobUrl falls back to apply_link when the job id is missing", () => {
  assert.equal(
    getOpenJobUrl({ apply_link: "https://example.com/apply" }),
    "https://example.com/apply"
  );
});

test("getOpenJobUrl returns an empty string when no usable URL exists", () => {
  assert.equal(getOpenJobUrl({}), "");
});
