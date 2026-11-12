/**
 * The run directory a new chat names, in AutoCode's own shape:
 * `YYYYMMDD-HHMMSS-vscode-XXXXXX` under the workspace's runs directory.
 *
 * Date and time are both local, read from one clock reading, as the core names
 * its own runs (`autocode_run_setup`) and as `runs` reads a name back. Taking
 * the date from UTC and the time from the local clock named an evening chat in
 * California with tomorrow's date.
 */
export function freshRunDir(workspace: string, now: Date = new Date(), random: () => number = Math.random): string {
  const two = (value: number) => String(value).padStart(2, "0");
  const date = `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}`;
  const time = `${two(now.getHours())}${two(now.getMinutes())}${two(now.getSeconds())}`;
  const suffix = random().toString(16).slice(2, 8).padEnd(6, "0");
  return `${workspace}/.autocode/runs/${date}-${time}-vscode-${suffix}`;
}
