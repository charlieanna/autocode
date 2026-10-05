const args = process.argv.slice(2);
if (args.length !== 1) {
  process.stderr.write("usage: greet.py NAME\n");
  process.exit(2);
}
process.stdout.write("Hello, " + args[0] + "\n");
