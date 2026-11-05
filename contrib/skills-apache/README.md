# Skills under the Apache License 2.0

The rest of this repository is MIT. The files in **this folder** are different: they are adapted
from [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official), where
each plugin is licensed under the Apache License, Version 2.0. The full license is in [`LICENSE`](LICENSE),
and it applies to everything in this folder.

| Skill | Adapted from | Licence |
|---|---|---|
| [`code-simplifier`](code-simplifier/SKILL.md) | `plugins/code-simplifier/agents/code-simplifier.md` (commit `fa4eb54`) | Apache-2.0 |

The changes are listed in a notice at the top of each `SKILL.md`. That notice sits in the file's YAML
comments, so it is never sent to Vibe. This work is not endorsed by Anthropic.

`code-simplifier` is a single-agent skill: one pass, in the same session, on the code your task names.

## Install

```sh
mkdir -p ~/.vibe/skills && cp -r contrib/skills-apache/code-simplifier ~/.vibe/skills/
```

Then use it with `vibe_submit(skill="code-simplifier", spec="Simplify the changes in HEAD~1..HEAD ...")`.
