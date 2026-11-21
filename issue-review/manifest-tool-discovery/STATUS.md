# Manifest tool discovery: issue #4038

The local candidate starts from #4024 head `99445d25eaa458ca65ebe4ded0ca1536725f8375`. It is not published or merged. Publish only after #4024 is qualified and integrated, so the earlier host-side rustup probe is not reintroduced.

An approved command using a qualified tool path should not additionally require a bare executable merely because its matching manifest exists. Discovery now omits only the manifest default for a program family already named by the approved commands. Other manifest defaults and mandatory runtime helpers still apply.

Verification so far:

- The three Cargo, Go and npm controls fail on the 994 base and pass on the candidate. These mock executable lookup and native-loader discovery; they are not real Go or npm executions. Evidence: `/private/tmp/autocode-manifest-family-994-before.log` and `/private/tmp/autocode-manifest-family-after.log`.
- A real macOS Seatbelt check with a `Cargo.toml` and the selected Cargo proxy outside PATH runs the explicit installed `cargo +stable --version` successfully on the candidate. The original discovery refusal and corrected run are in `/private/tmp/autocode-manifest-absolute-discovery.log` and `/private/tmp/autocode-manifest-absolute-after.log`.
- Controls preserve the manifest requirement when no program is named, a different manifest's requirement, and npm's Node helper. The 66 focused toolchain, plan, architecture and state tests pass: `/private/tmp/autocode-manifest-family-focused.log`.
- Actual VS Code extension verification and exact-head required CI remain outstanding. The full user-requested outcome has not been established.
