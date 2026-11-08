# Native OpenAI subscription authentication

The isolated native role launcher disabled OpenCode's default plugins for every
provider. On pinned OpenCode 1.18.33 this also disables its compiled-in
`CodexAuthPlugin`, which supplies the OpenAI OAuth subscription route. A saved
login therefore existed while model preflight reported no OpenAI provider.

Keep that built-in authentication available for explicitly selected `openai/`
roles. Other provider roles retain the previous disable flag. The executable,
SDK, exclusive configuration tree, external plugin list, enabled provider,
permissions, launch identity and image receipts remain pinned and checked.
This does not authorize arbitrary plugins or replace native image acceptance.

Controlled regressions cover OpenAI staging and final launch verification,
including an inherited disable flag, and unchanged GLM/MiMo isolation.
The live Figma qualification and final image acceptance remain separate gates.
