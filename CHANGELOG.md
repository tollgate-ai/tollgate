# Changelog

## [0.14.3](https://github.com/tollgate-ai/tollgate/compare/v0.14.2...v0.14.3) (2026-11-28)


### Bug Fixes

* **checks:** count a member of a seam as a seam name ([#180](https://github.com/tollgate-ai/tollgate/issues/180)) ([dd67774](https://github.com/tollgate-ai/tollgate/commit/dd6777497c6916b60d2c1907bf10f901bb409d74))
* **driver:** key each review on the content it judges ([#179](https://github.com/tollgate-ai/tollgate/issues/179)) ([c8e2a15](https://github.com/tollgate-ai/tollgate/commit/c8e2a153b0463834981bd1462d229c7d3491b382))

## [0.14.2](https://github.com/tollgate-ai/tollgate/compare/v0.14.1...v0.14.2) (2026-11-28)


### Bug Fixes

* **checks:** count a seam's owner and a generic type's field as seam names ([#177](https://github.com/tollgate-ai/tollgate/issues/177)) ([342dcad](https://github.com/tollgate-ai/tollgate/commit/342dcade053a84269dd497ee1c50b2f51840a1cd))

## [0.14.1](https://github.com/tollgate-ai/tollgate/compare/v0.14.0...v0.14.1) (2026-11-28)


### Bug Fixes

* **guard:** let a role run commands in its own copy while several steps wait ([#174](https://github.com/tollgate-ai/tollgate/issues/174)) ([27a626a](https://github.com/tollgate-ai/tollgate/commit/27a626a7de96c57dc48f59c77e3496775d560203))

## [0.14.0](https://github.com/tollgate-ai/tollgate/compare/v0.13.3...v0.14.0) (2026-11-28)


### Features

* **session:** let several tasks of one project wait at once ([#172](https://github.com/tollgate-ai/tollgate/issues/172)) ([b500961](https://github.com/tollgate-ai/tollgate/commit/b500961730739efb7746889a4eebb53ae66ca914))

## [0.13.3](https://github.com/tollgate-ai/tollgate/compare/v0.13.2...v0.13.3) (2026-11-28)


### Bug Fixes

* **driver:** route each problem to the role that can fix it ([#169](https://github.com/tollgate-ai/tollgate/issues/169)) ([3c34cfa](https://github.com/tollgate-ai/tollgate/commit/3c34cfa0c03ea91f609f78085d2a1492a10e26a0))

## [0.13.2](https://github.com/tollgate-ai/tollgate/compare/v0.13.1...v0.13.2) (2026-11-27)


### Bug Fixes

* **session:** keep the events a replay holds when it never reaches its step ([#166](https://github.com/tollgate-ai/tollgate/issues/166)) ([0683ffe](https://github.com/tollgate-ai/tollgate/commit/0683ffef85c1abb522e7c1022e62fedf5bced6b4))
* **task:** write and review a plan sent back before a new decision ([#168](https://github.com/tollgate-ai/tollgate/issues/168)) ([537390e](https://github.com/tollgate-ai/tollgate/commit/537390ea420264bfb4c7b142a8239690cb419859))

## [0.13.1](https://github.com/tollgate-ai/tollgate/compare/v0.13.0...v0.13.1) (2026-11-27)


### Bug Fixes

* **driver:** replay the plan's writing in session mode so review stops at four rounds ([#164](https://github.com/tollgate-ai/tollgate/issues/164)) ([d3d815e](https://github.com/tollgate-ai/tollgate/commit/d3d815e28aad48dd9118e494c68484e02880fe52))

## [0.13.0](https://github.com/tollgate-ai/tollgate/compare/v0.12.1...v0.13.0) (2026-11-27)


### Features

* **diagram:** draw diagrams as SVG, in light and dark ([f7c68e1](https://github.com/tollgate-ai/tollgate/commit/f7c68e1bb2652840c5918e2242319d1527f8d669))
* **driver:** analyze, plan, and build each ticket until it passes ([9f5c0ae](https://github.com/tollgate-ai/tollgate/commit/9f5c0ae6070b9948aa43b5042b1ea572c945c751))
* **gate:** the gate, the store, and the formats it reads ([8766328](https://github.com/tollgate-ai/tollgate/commit/8766328d81d694a19c845205f4e861695b0053cf))
* **hooks:** the guard and the policy of each role ([8901f93](https://github.com/tollgate-ai/tollgate/commit/8901f93bca19580be4980c0b2c257ca9e73ccdc2))
* **roles:** the roles, their prompts, and the review lenses ([fb9a103](https://github.com/tollgate-ai/tollgate/commit/fb9a103419ff963ffb99aaa38dc770c78dbd0b83))
* **setup:** set up a project, and run the checks of each git step ([6c1bee0](https://github.com/tollgate-ai/tollgate/commit/6c1bee0b31003e0876cbc9ba8fb4c64b31b0edc4))
* **skills:** the commands you type in Claude Code ([1251c88](https://github.com/tollgate-ai/tollgate/commit/1251c88449dc324c88176146cee06a37fe082b28))

## [0.12.1](https://github.com/tollgate-ai/tollgate/compare/v0.12.0...v0.12.1) (2026-11-27)


### Bug Fixes

* **checks:** skip the Go "too many errors" line in a seam error ([6ea196e](https://github.com/tollgate-ai/tollgate/commit/6ea196e60afba661e483c2e03ffa50221d530379))
* **checks:** skip the golangci-lint package header in a seam error ([3abb338](https://github.com/tollgate-ai/tollgate/commit/3abb3389b537acd9241da653b4c7636463c2b61e))
* **driver:** let a locked test name a seam that is not built yet ([9239806](https://github.com/tollgate-ai/tollgate/commit/9239806205271caea9d0c50f916279eb09bc80ac))
* **freeze:** lock Go tests whose package needs a seam to compile ([4b8faff](https://github.com/tollgate-ai/tollgate/commit/4b8fafff944c298722c35108163e9cbe0c236ec4))
* **freeze:** read a Go build failure from the output line too ([8525c13](https://github.com/tollgate-ai/tollgate/commit/8525c13ffa20e9c4c07a0a99bfea7af58bf57237))

## [0.12.0](https://github.com/tollgate-ai/tollgate/compare/v0.11.2...v0.12.0) (2026-11-26)


### ⚠ BREAKING CHANGES

* **gate:** add the spec setting, and remove signers and owners ([#130](https://github.com/tollgate-ai/tollgate/issues/130))
* **tickets:** ask each ticket for the kinds of test it needs ([#121](https://github.com/tollgate-ai/tollgate/issues/121))
* **driver:** run Tollgate's roles as subagents in your own session ([#115](https://github.com/tollgate-ai/tollgate/issues/115))

### Features

* **agents:** ship each role as a Claude Code subagent ([#111](https://github.com/tollgate-ai/tollgate/issues/111)) ([4728621](https://github.com/tollgate-ai/tollgate/commit/4728621a80a27fe8c5fd2efe31e8dfc59b9000b9))
* **brainstorm:** turn a rough idea into a brief ([#128](https://github.com/tollgate-ai/tollgate/issues/128)) ([5f1a844](https://github.com/tollgate-ai/tollgate/commit/5f1a8448f1fc4a35adbbd087e7b2156d957af1e5))
* **diagram:** draw C4, use case, class, and ER diagrams, and a C4 context as a scene ([#122](https://github.com/tollgate-ai/tollgate/issues/122)) ([31313e1](https://github.com/tollgate-ai/tollgate/commit/31313e19b75f95265bc3e8ae49c12d3af6ce4470))
* **diagram:** draw diagrams as SVG, in light and dark ([b3b5d0f](https://github.com/tollgate-ai/tollgate/commit/b3b5d0f72d590a2acd50908375a382fc5d2e74a5))
* **diagram:** draw UML sequence diagrams and state machines ([#109](https://github.com/tollgate-ai/tollgate/issues/109)) ([ad9722b](https://github.com/tollgate-ai/tollgate/commit/ad9722b1aa559fdd9e4774d7eda7d64d0f824e2a))
* **diagram:** moving dots on every arrow, and one Tollgate hook run from setup/ ([#150](https://github.com/tollgate-ai/tollgate/issues/150)) ([088b120](https://github.com/tollgate-ai/tollgate/commit/088b1203559aace06a093d1c9783695c2ce2317c))
* **docs:** add the decision record step, and close gaps in the spec rules ([#132](https://github.com/tollgate-ai/tollgate/issues/132)) ([176cd00](https://github.com/tollgate-ai/tollgate/commit/176cd004a4f7b8a989a3d1af2b8de7fefb7feea8))
* **docs:** make a task write what it adds into the spec docs ([#135](https://github.com/tollgate-ai/tollgate/issues/135)) ([cc1c2c9](https://github.com/tollgate-ai/tollgate/commit/cc1c2c9560530ff50446d9a58c4fbe0860ccacba))
* **driver:** analyze, plan, and build each ticket until it passes ([6d95ad0](https://github.com/tollgate-ai/tollgate/commit/6d95ad0fb33de727f64e34499e41ba86bcb55b6d))
* **driver:** run Tollgate's roles as subagents in your own session ([#115](https://github.com/tollgate-ai/tollgate/issues/115)) ([db50916](https://github.com/tollgate-ai/tollgate/commit/db50916f88751370a26dbe28ea618a1587af8c1e))
* **driver:** the builder writes a diagram's spec, and Tollgate draws it ([#98](https://github.com/tollgate-ai/tollgate/issues/98)) ([3a5ea13](https://github.com/tollgate-ai/tollgate/commit/3a5ea133454b0ecf56c3afd65d98292c3adc4815))
* **gate:** add the spec setting, and remove signers and owners ([#130](https://github.com/tollgate-ai/tollgate/issues/130)) ([bda3ac5](https://github.com/tollgate-ai/tollgate/commit/bda3ac5abf9fe7e78b31ea1e14d1083b8bde8684))
* **gate:** the gate, the store, and the formats it reads ([d7a0ec9](https://github.com/tollgate-ai/tollgate/commit/d7a0ec92888de7f2c1a55475c7e02a66e1f9af06))
* **hooks:** apply each role's rules by agent type, and let your session push a branch ([#113](https://github.com/tollgate-ai/tollgate/issues/113)) ([72a6148](https://github.com/tollgate-ai/tollgate/commit/72a614848a6741942b6b26fdc97e6b1c0e4654a1))
* **hooks:** the guard and the policy of each role ([d37a9b5](https://github.com/tollgate-ai/tollgate/commit/d37a9b5f0a4a862b63b413372afcde4511744dc1))
* **init:** run the project's checks side by side before a push ([#144](https://github.com/tollgate-ai/tollgate/issues/144)) ([5f26250](https://github.com/tollgate-ai/tollgate/commit/5f26250019200c97ffa7215d1c6108d548057201))
* **settings:** run background roles in Docker's Claude Code image by default ([#118](https://github.com/tollgate-ai/tollgate/issues/118)) ([86d96cf](https://github.com/tollgate-ai/tollgate/commit/86d96cfc3c0d48334bc05a1b0acb09d2c26b4a67))
* **setup:** check the words drawn in a diagram against the word list ([#124](https://github.com/tollgate-ai/tollgate/issues/124)) ([556a87b](https://github.com/tollgate-ai/tollgate/commit/556a87b10a33d4fcf6d83f6b94d38309d9211749))
* **setup:** set up a project, and check each commit with pre-commit ([16a6b07](https://github.com/tollgate-ai/tollgate/commit/16a6b0755fcdf1e81012fb266e0126d6d3b74d0f))
* **setup:** the project's checks are hooks of its own pre-commit config ([#152](https://github.com/tollgate-ai/tollgate/issues/152)) ([9c746af](https://github.com/tollgate-ai/tollgate/commit/9c746af488f10212aa666445f5c558a177219e6a))
* **skills:** the commands you type in Claude Code ([a8d32b2](https://github.com/tollgate-ai/tollgate/commit/a8d32b2db613b09bd72e4312a2848f66bbcf40c1))
* **tickets:** ask each ticket for the kinds of test it needs ([#121](https://github.com/tollgate-ai/tollgate/issues/121)) ([bddd463](https://github.com/tollgate-ai/tollgate/commit/bddd463fdd17766cfac78dc58980c7bcc15cdce9))


### Bug Fixes

* **analysis:** let a fact about a missing item cite its folder, and say how to fix a refused analysis ([#139](https://github.com/tollgate-ai/tollgate/issues/139)) ([7319177](https://github.com/tollgate-ai/tollgate/commit/73191773cb6a163b91a2d5717f2a491769aea1dc))
* **checks:** stop the local git settings from changing the checks and the tests ([#133](https://github.com/tollgate-ai/tollgate/issues/133)) ([8136b0f](https://github.com/tollgate-ai/tollgate/commit/8136b0f5e94c8553e0703bb97f4641790a688550))
* **diagram:** draw pre-commit as its own object, and keep one meaning for each object ([#127](https://github.com/tollgate-ai/tollgate/issues/127)) ([e9a87e9](https://github.com/tollgate-ai/tollgate/commit/e9a87e9f471048041384e899c823939628e3d432))
* **diagram:** pin each label to the center of its part ([#97](https://github.com/tollgate-ai/tollgate/issues/97)) ([b28372a](https://github.com/tollgate-ai/tollgate/commit/b28372a53192c9a73925b1c735f92dea9012b14a))
* **driver:** never let two drivers hold one run lock ([#120](https://github.com/tollgate-ai/tollgate/issues/120)) ([479642d](https://github.com/tollgate-ai/tollgate/commit/479642daa6f532c33f22de940f08b59cc6789799))
* **gate:** read citations and checklist marks without slow backtracking ([#101](https://github.com/tollgate-ai/tollgate/issues/101)) ([f240334](https://github.com/tollgate-ai/tollgate/commit/f240334c8668b64557f19de27684dc4f55381648))
* **init:** run the hook checks with uv ([#143](https://github.com/tollgate-ai/tollgate/issues/143)) ([442ea27](https://github.com/tollgate-ai/tollgate/commit/442ea279c77ebc4d1da7a89e0d96d6b829b1805f))
* **init:** run Tollgate's checks from one script, and keep the project's checks as plain hooks ([425af0b](https://github.com/tollgate-ai/tollgate/commit/425af0ba3548dc9fbd284bbb1a08fc45157295e7))
* **init:** set up Tollgate's own project, which runs its checks from setup/ ([#154](https://github.com/tollgate-ai/tollgate/issues/154)) ([2f5b377](https://github.com/tollgate-ai/tollgate/commit/2f5b377891c197fcd4c3b10231bffe60e40e6c1b))
* **init:** write the settings from a draft file, so the guard stays closed ([#137](https://github.com/tollgate-ai/tollgate/issues/137)) ([db81be9](https://github.com/tollgate-ai/tollgate/commit/db81be93bd750a0dab90b7b2ba707f3a497ab2d8))
* **plan:** send a plan that breaks a rule back to the planner, not stop the task ([#141](https://github.com/tollgate-ai/tollgate/issues/141)) ([0f02602](https://github.com/tollgate-ai/tollgate/commit/0f02602a323b2dd3bfc52d6c9b726bf89c2310f3))
* **session:** log each event once on a replay, and let existing tests cover a kept behavior ([#125](https://github.com/tollgate-ai/tollgate/issues/125)) ([edbeb06](https://github.com/tollgate-ai/tollgate/commit/edbeb06a29ec65e4c7c3ffe3d2a04bd4f78aa7bf))
* **setup:** skip CHANGELOG.md in the writing check ([#108](https://github.com/tollgate-ai/tollgate/issues/108)) ([b0e0cbb](https://github.com/tollgate-ai/tollgate/commit/b0e0cbb0f95a388046404f51a02217500643ea3b))
* **setup:** skip pip requirements files in the writing check ([#104](https://github.com/tollgate-ai/tollgate/issues/104)) ([e9a17a2](https://github.com/tollgate-ai/tollgate/commit/e9a17a2ce35285317c6db18ac891e7821457360d))
* **setup:** stop a home from showing a change each time a project moves on ([#114](https://github.com/tollgate-ai/tollgate/issues/114)) ([970a3d3](https://github.com/tollgate-ai/tollgate/commit/970a3d34c0681e3978627a118e56da998a4d337a))
* **skills:** run the plugin's Python code, not a project package of the same name ([#107](https://github.com/tollgate-ai/tollgate/issues/107)) ([0207f33](https://github.com/tollgate-ai/tollgate/commit/0207f33823f0c39eba08c286e244642b711f0fbe))
* **task:** run a roadmap in session mode, the same as a task ([#157](https://github.com/tollgate-ai/tollgate/issues/157)) ([f709c3d](https://github.com/tollgate-ai/tollgate/commit/f709c3d04ff54a8579f55d81283bfd14d24bdcd9))
* **task:** send back a plan that leaves the required README missing, and replay it ([#126](https://github.com/tollgate-ai/tollgate/issues/126)) ([8f6d87a](https://github.com/tollgate-ai/tollgate/commit/8f6d87a6741110d37483c684f101ffb20a522bc5))
* **tests:** start the tests without the git location variables ([#147](https://github.com/tollgate-ai/tollgate/issues/147)) ([8e15561](https://github.com/tollgate-ai/tollgate/commit/8e1556105f400bccfa6f1fe7759c31285e4f54ea))

## [0.11.2](https://github.com/tollgate-ai/tollgate/compare/v0.11.1...v0.11.2) (2026-11-26)


### Bug Fixes

* **task:** run a roadmap in session mode, the same as a task ([#157](https://github.com/tollgate-ai/tollgate/issues/157)) ([f709c3d](https://github.com/tollgate-ai/tollgate/commit/f709c3d04ff54a8579f55d81283bfd14d24bdcd9))

## [0.11.1](https://github.com/tollgate-ai/tollgate/compare/v0.11.0...v0.11.1) (2026-11-26)


### Bug Fixes

* **init:** set up Tollgate's own project, which runs its checks from setup/ ([#154](https://github.com/tollgate-ai/tollgate/issues/154)) ([2f5b377](https://github.com/tollgate-ai/tollgate/commit/2f5b377891c197fcd4c3b10231bffe60e40e6c1b))

## [0.11.0](https://github.com/tollgate-ai/tollgate/compare/v0.10.0...v0.11.0) (2026-11-26)


### Features

* **setup:** the project's checks are hooks of its own pre-commit config ([#152](https://github.com/tollgate-ai/tollgate/issues/152)) ([9c746af](https://github.com/tollgate-ai/tollgate/commit/9c746af488f10212aa666445f5c558a177219e6a))

## [0.10.0](https://github.com/tollgate-ai/tollgate/compare/v0.9.2...v0.10.0) (2026-11-25)


### Features

* **diagram:** moving dots on every arrow, and one Tollgate hook run from setup/ ([#150](https://github.com/tollgate-ai/tollgate/issues/150)) ([088b120](https://github.com/tollgate-ai/tollgate/commit/088b1203559aace06a093d1c9783695c2ce2317c))

## [0.9.2](https://github.com/tollgate-ai/tollgate/compare/v0.9.1...v0.9.2) (2026-11-25)


### Bug Fixes

* **tests:** start the tests without the git location variables ([#147](https://github.com/tollgate-ai/tollgate/issues/147)) ([8e15561](https://github.com/tollgate-ai/tollgate/commit/8e1556105f400bccfa6f1fe7759c31285e4f54ea))

## [0.9.1](https://github.com/tollgate-ai/tollgate/compare/v0.9.0...v0.9.1) (2026-11-25)


### Bug Fixes

* **init:** run Tollgate's checks from one script, and keep the project's checks as plain hooks ([425af0b](https://github.com/tollgate-ai/tollgate/commit/425af0ba3548dc9fbd284bbb1a08fc45157295e7))

## [0.9.0](https://github.com/tollgate-ai/tollgate/compare/v0.8.2...v0.9.0) (2026-11-25)


### Features

* **init:** run the project's checks side by side before a push ([#144](https://github.com/tollgate-ai/tollgate/issues/144)) ([5f26250](https://github.com/tollgate-ai/tollgate/commit/5f26250019200c97ffa7215d1c6108d548057201))


### Bug Fixes

* **init:** run the hook checks with uv ([#143](https://github.com/tollgate-ai/tollgate/issues/143)) ([442ea27](https://github.com/tollgate-ai/tollgate/commit/442ea279c77ebc4d1da7a89e0d96d6b829b1805f))
* **plan:** send a plan that breaks a rule back to the planner, not stop the task ([#141](https://github.com/tollgate-ai/tollgate/issues/141)) ([0f02602](https://github.com/tollgate-ai/tollgate/commit/0f02602a323b2dd3bfc52d6c9b726bf89c2310f3))

## [0.8.2](https://github.com/tollgate-ai/tollgate/compare/v0.8.1...v0.8.2) (2026-11-25)


### Bug Fixes

* **analysis:** let a fact about a missing item cite its folder, and say how to fix a refused analysis ([#139](https://github.com/tollgate-ai/tollgate/issues/139)) ([7319177](https://github.com/tollgate-ai/tollgate/commit/73191773cb6a163b91a2d5717f2a491769aea1dc))

## [0.8.1](https://github.com/tollgate-ai/tollgate/compare/v0.8.0...v0.8.1) (2026-11-25)


### Bug Fixes

* **init:** write the settings from a draft file, so the guard stays closed ([#137](https://github.com/tollgate-ai/tollgate/issues/137)) ([db81be9](https://github.com/tollgate-ai/tollgate/commit/db81be93bd750a0dab90b7b2ba707f3a497ab2d8))

## [0.8.0](https://github.com/tollgate-ai/tollgate/compare/v0.7.0...v0.8.0) (2026-11-25)


### Features

* **docs:** make a task write what it adds into the spec docs ([#135](https://github.com/tollgate-ai/tollgate/issues/135)) ([cc1c2c9](https://github.com/tollgate-ai/tollgate/commit/cc1c2c9560530ff50446d9a58c4fbe0860ccacba))

## [0.7.0](https://github.com/tollgate-ai/tollgate/compare/v0.6.0...v0.7.0) (2026-11-24)


### ⚠ BREAKING CHANGES

* **gate:** add the spec setting, and remove signers and owners ([#130](https://github.com/tollgate-ai/tollgate/issues/130))

### Features

* **docs:** add the decision record step, and close gaps in the spec rules ([#132](https://github.com/tollgate-ai/tollgate/issues/132)) ([176cd00](https://github.com/tollgate-ai/tollgate/commit/176cd004a4f7b8a989a3d1af2b8de7fefb7feea8))
* **gate:** add the spec setting, and remove signers and owners ([#130](https://github.com/tollgate-ai/tollgate/issues/130)) ([bda3ac5](https://github.com/tollgate-ai/tollgate/commit/bda3ac5abf9fe7e78b31ea1e14d1083b8bde8684))


### Bug Fixes

* **checks:** stop the local git settings from changing the checks and the tests ([#133](https://github.com/tollgate-ai/tollgate/issues/133)) ([8136b0f](https://github.com/tollgate-ai/tollgate/commit/8136b0f5e94c8553e0703bb97f4641790a688550))

## [0.6.0](https://github.com/tollgate-ai/tollgate/compare/v0.5.0...v0.6.0) (2026-11-23)


### Features

* **brainstorm:** turn a rough idea into a brief ([#128](https://github.com/tollgate-ai/tollgate/issues/128)) ([5f1a844](https://github.com/tollgate-ai/tollgate/commit/5f1a8448f1fc4a35adbbd087e7b2156d957af1e5))


### Bug Fixes

* **diagram:** draw pre-commit as its own object, and keep one meaning for each object ([#127](https://github.com/tollgate-ai/tollgate/issues/127)) ([e9a87e9](https://github.com/tollgate-ai/tollgate/commit/e9a87e9f471048041384e899c823939628e3d432))
* **task:** send back a plan that leaves the required README missing, and replay it ([#126](https://github.com/tollgate-ai/tollgate/issues/126)) ([8f6d87a](https://github.com/tollgate-ai/tollgate/commit/8f6d87a6741110d37483c684f101ffb20a522bc5))

## [0.5.0](https://github.com/tollgate-ai/tollgate/compare/v0.4.0...v0.5.0) (2026-11-23)


### Features

* **diagram:** draw C4, use case, class, and ER diagrams, and a C4 context as a scene ([#122](https://github.com/tollgate-ai/tollgate/issues/122)) ([31313e1](https://github.com/tollgate-ai/tollgate/commit/31313e19b75f95265bc3e8ae49c12d3af6ce4470))
* **setup:** check the words drawn in a diagram against the word list ([#124](https://github.com/tollgate-ai/tollgate/issues/124)) ([556a87b](https://github.com/tollgate-ai/tollgate/commit/556a87b10a33d4fcf6d83f6b94d38309d9211749))


### Bug Fixes

* **session:** log each event once on a replay, and let existing tests cover a kept behavior ([#125](https://github.com/tollgate-ai/tollgate/issues/125)) ([edbeb06](https://github.com/tollgate-ai/tollgate/commit/edbeb06a29ec65e4c7c3ffe3d2a04bd4f78aa7bf))

## [0.4.0](https://github.com/tollgate-ai/tollgate/compare/v0.3.0...v0.4.0) (2026-11-23)


### ⚠ BREAKING CHANGES

* **tickets:** ask each ticket for the kinds of test it needs ([#121](https://github.com/tollgate-ai/tollgate/issues/121))

### Features

* **settings:** run background roles in Docker's Claude Code image by default ([#118](https://github.com/tollgate-ai/tollgate/issues/118)) ([86d96cf](https://github.com/tollgate-ai/tollgate/commit/86d96cfc3c0d48334bc05a1b0acb09d2c26b4a67))
* **tickets:** ask each ticket for the kinds of test it needs ([#121](https://github.com/tollgate-ai/tollgate/issues/121)) ([bddd463](https://github.com/tollgate-ai/tollgate/commit/bddd463fdd17766cfac78dc58980c7bcc15cdce9))


### Bug Fixes

* **driver:** never let two drivers hold one run lock ([#120](https://github.com/tollgate-ai/tollgate/issues/120)) ([479642d](https://github.com/tollgate-ai/tollgate/commit/479642daa6f532c33f22de940f08b59cc6789799))

## [0.3.0](https://github.com/tollgate-ai/tollgate/compare/v0.2.0...v0.3.0) (2026-11-22)


### ⚠ BREAKING CHANGES

* **driver:** run Tollgate's roles as subagents in your own session ([#115](https://github.com/tollgate-ai/tollgate/issues/115))

### Features

* **agents:** ship each role as a Claude Code subagent ([#111](https://github.com/tollgate-ai/tollgate/issues/111)) ([4728621](https://github.com/tollgate-ai/tollgate/commit/4728621a80a27fe8c5fd2efe31e8dfc59b9000b9))
* **driver:** run Tollgate's roles as subagents in your own session ([#115](https://github.com/tollgate-ai/tollgate/issues/115)) ([db50916](https://github.com/tollgate-ai/tollgate/commit/db50916f88751370a26dbe28ea618a1587af8c1e))
* **hooks:** apply each role's rules by agent type, and let your session push a branch ([#113](https://github.com/tollgate-ai/tollgate/issues/113)) ([72a6148](https://github.com/tollgate-ai/tollgate/commit/72a614848a6741942b6b26fdc97e6b1c0e4654a1))


### Bug Fixes

* **setup:** stop a home from showing a change each time a project moves on ([#114](https://github.com/tollgate-ai/tollgate/issues/114)) ([970a3d3](https://github.com/tollgate-ai/tollgate/commit/970a3d34c0681e3978627a118e56da998a4d337a))

## [0.2.0](https://github.com/tollgate-ai/tollgate/compare/v0.1.1...v0.2.0) (2026-11-22)


### Features

* **diagram:** draw UML sequence diagrams and state machines ([#109](https://github.com/tollgate-ai/tollgate/issues/109)) ([ad9722b](https://github.com/tollgate-ai/tollgate/commit/ad9722b1aa559fdd9e4774d7eda7d64d0f824e2a))

## [0.1.1](https://github.com/tollgate-ai/tollgate/compare/v0.1.0...v0.1.1) (2026-11-22)


### Bug Fixes

* **setup:** skip CHANGELOG.md in the writing check ([#108](https://github.com/tollgate-ai/tollgate/issues/108)) ([b0e0cbb](https://github.com/tollgate-ai/tollgate/commit/b0e0cbb0f95a388046404f51a02217500643ea3b))
* **setup:** skip pip requirements files in the writing check ([#104](https://github.com/tollgate-ai/tollgate/issues/104)) ([e9a17a2](https://github.com/tollgate-ai/tollgate/commit/e9a17a2ce35285317c6db18ac891e7821457360d))
* **skills:** run the plugin's Python code, not a project package of the same name ([#107](https://github.com/tollgate-ai/tollgate/issues/107)) ([0207f33](https://github.com/tollgate-ai/tollgate/commit/0207f33823f0c39eba08c286e244642b711f0fbe))

## 0.1.0 (2026-11-22)


### Features

* **diagram:** draw diagrams as SVG, in light and dark ([b3b5d0f](https://github.com/tollgate-ai/tollgate/commit/b3b5d0f72d590a2acd50908375a382fc5d2e74a5))
* **driver:** analyze, plan, and build each ticket until it passes ([6d95ad0](https://github.com/tollgate-ai/tollgate/commit/6d95ad0fb33de727f64e34499e41ba86bcb55b6d))
* **driver:** the builder writes a diagram's spec, and Tollgate draws it ([#98](https://github.com/tollgate-ai/tollgate/issues/98)) ([3a5ea13](https://github.com/tollgate-ai/tollgate/commit/3a5ea133454b0ecf56c3afd65d98292c3adc4815))
* **gate:** the gate, the store, and the formats it reads ([d7a0ec9](https://github.com/tollgate-ai/tollgate/commit/d7a0ec92888de7f2c1a55475c7e02a66e1f9af06))
* **hooks:** the guard and the policy of each role ([d37a9b5](https://github.com/tollgate-ai/tollgate/commit/d37a9b5f0a4a862b63b413372afcde4511744dc1))
* **setup:** set up a project, and check each commit with pre-commit ([16a6b07](https://github.com/tollgate-ai/tollgate/commit/16a6b0755fcdf1e81012fb266e0126d6d3b74d0f))
* **skills:** the commands you type in Claude Code ([a8d32b2](https://github.com/tollgate-ai/tollgate/commit/a8d32b2db613b09bd72e4312a2848f66bbcf40c1))


### Bug Fixes

* **diagram:** pin each label to the center of its part ([#97](https://github.com/tollgate-ai/tollgate/issues/97)) ([b28372a](https://github.com/tollgate-ai/tollgate/commit/b28372a53192c9a73925b1c735f92dea9012b14a))
* **gate:** read citations and checklist marks without slow backtracking ([#101](https://github.com/tollgate-ai/tollgate/issues/101)) ([f240334](https://github.com/tollgate-ai/tollgate/commit/f240334c8668b64557f19de27684dc4f55381648))
