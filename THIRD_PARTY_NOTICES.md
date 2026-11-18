# Third-party notices and provenance

Project-authored source is MIT licensed; see [LICENSE](LICENSE). Dependencies
retain their own licenses. Package installation downloads dependencies rather
than copying a private environment into this export.

## Runtime and build dependencies

The Python runtime uses Click (BSD-3-Clause), Playwright (Apache-2.0), and its
Greenlet dependency (MIT and Python-2.0). Development tools include pytest
(MIT), Ruff (MIT), and build (MIT). Python versions are range-resolved except
Ruff 0.15.5; retain installed distribution license files when redistributing
those distributions. Playwright's downloaded driver includes further notices;
this source export does not bundle its driver or browsers.

The Node lockfiles record exact versions, registry URLs, integrity hashes, and
licenses, including transitive and platform-optional dependencies. The backend
uses better-sqlite3, cors, cron-parser, Express, multer, and node-cron. The
frontend uses React and React DOM; Vite and its React plugin build the assets.
These direct packages declare MIT. Transitive licenses are not all MIT.

In particular, frontend `caniuse-lite` declares **CC-BY-4.0**. Its browser
support dataset comes from [Can I use](https://caniuse.com/), maintained by
Alexis Deveria and contributors. See the
[upstream dataset](https://github.com/browserslist/caniuse-lite) and
[CC BY 4.0 terms](https://creativecommons.org/licenses/by/4.0/).
Review attribution and whether dataset material enters any redistributed
artifact; this entry is not a blanket MIT relicensing. Other transitive
licenses include ISC, BSD, Apache-2.0, and alternative-license expressions.

The reviewed lockfiles contain 228 dependency entries, counting platform
alternatives and each package scope separately: 206 MIT, 13 ISC, three
Apache-2.0, three BSD-3-Clause, one MIT-or-WTFPL, one
BSD-2-Clause-or-MIT-or-Apache-2.0, and one CC-BY-4.0. `busboy` 1.6.0 and
`streamsearch` 1.1.0 omit a license field; their installed LICENSE files grant
MIT terms, copyright Brian White. The MIT total includes these two reviews.
`caniuse-lite` 1.0.30001777 names Ben Briggs as package author; its dataset
attribution and upstream license remain distinct from the project license.

The refreshed graph uses Express 4.22.3, multer 2.5.0, node-cron 4.6.0,
Vite 6.4.6, and the React plugin 4.7.0. node-cron 4 replaces the former
uuid dependency with the platform crypto API. Vite 6 adds tinyglobby, fdir,
and picomatch under MIT terms; the complete versioned inventory and retained
installed license texts accompany the local release evidence.

React's production bundle retains its license banner. If you distribute
compiled assets or vendored dependencies, retain the corresponding complete
license and notice texts, including React, React DOM, Scheduler, SQLite,
native SQLite bindings, and any bundled build-tool material. The inventory
and artifact review must be refreshed when either lockfile changes.

## React, React DOM, and Scheduler

The compiled frontend includes these MIT-licensed packages. Their notice is:

Copyright (c) Facebook, Inc. and its affiliates.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Application material

The UI, runtime, and synthetic test/example content originate in this project.
No images, icon packs, fonts, real job descriptions, CVs, or external prompt
packs are intentionally bundled. See
[AI instruction provenance](docs/ai-instruction-provenance.md) for the retained
generation behavior. Third-party sites and APIs are external services, not
project-owned material. This notice does not grant rights to their content.

## Review status

The release review records the installed transitive inventory and retained
license texts separately from source. CC-BY dataset attribution and complete
notices for any future binary or vendored distribution require artifact-level
review before that distribution. The current deliverable is native source,
not an approved binary, container image, or publication.
