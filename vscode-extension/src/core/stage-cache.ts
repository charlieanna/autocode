/**
 * The new-chat stage list, kept per folder, backend and CLI, so a new chat shows its models at once.
 *
 * `autocode stages list --json` asks the backend which models its accounts serve; on OpenCode or
 * Kilo that is several seconds. Each new chat used to wait for its own call before it showed a
 * model. The list is the same for every new chat in a folder until a choice, the backend or the
 * accounts change, so the latest one is shown at once and read again in the background when it is
 * older than `MAX_AGE_MS`. The core still decides: a chat with its own picks reads its own list,
 * and is read again where it starts. The raw CLI output is kept, not the parsed view, so an
 * extension update reads it with its own parser; the newest `MAX_ENTRIES` lists are kept.
 */

import { readStages, type StageView } from "./stages";

/** How long a list is shown without reading it again in the background. */
export const MAX_AGE_MS = 60_000;
export const MAX_ENTRIES = 8;
/** The workspace state key the lists are kept under. */
export const STORAGE_KEY = "autocode.chat.stageLists";

interface Entry { stdout: string; at: number }

/** The part of a `vscode.Memento` the cache uses. */
export interface Store {
  get<T>(key: string): T | undefined;
  update(key: string, value: unknown): Thenable<void> | Promise<void>;
}

/** Which list a call reads: the folder it runs in, the CLI that answers and the exact arguments. */
export function listKey(cwd: string | undefined, command: string, prefix: string[], argv: string[]): string {
  return JSON.stringify([cwd ?? "", command, prefix, argv]);
}

export class StageListCache {
  private readonly entries = new Map<string, Entry>();
  private readonly pending = new Map<string, Promise<StageView>>();
  private readonly listeners = new Set<(key: string) => void>();
  /** Bumped by `expire`: a read started before it is not kept as fresh. */
  private generation = 0;

  constructor(private readonly store?: Store, private readonly now: () => number = Date.now) {
    const saved = store?.get<Record<string, Entry>>(STORAGE_KEY);
    if (saved && typeof saved === "object") {
      for (const [key, entry] of Object.entries(saved)) {
        // A saved list counts as stale: it is shown at once and read again.
        if (typeof entry?.stdout === "string") this.entries.set(key, { stdout: entry.stdout, at: 0 });
      }
    }
  }

  /** The latest list for `key`, or undefined when none was read or it no longer parses. */
  peek(key: string): StageView | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    try { return readStages(entry.stdout); } catch { this.entries.delete(key); return undefined; }
  }

  /** Whether `key`'s list was read recently enough not to read it again. */
  fresh(key: string): boolean {
    const entry = this.entries.get(key);
    return Boolean(entry && entry.at && this.now() - entry.at < MAX_AGE_MS);
  }

  /** Whether a read of `key` is under way. */
  loading(key: string): boolean { return this.pending.has(key); }

  /**
   * Read `key`'s list with `read` (the CLI's stdout); calls made while one is under way share it.
   * A list that does not parse is not kept and the call fails with the parser's reason.
   */
  load(key: string, read: () => Promise<string>): Promise<StageView> {
    const running = this.pending.get(key);
    if (running) return running;
    const generation = this.generation;
    const promise = (async () => {
      const stdout = await read();
      const view = readStages(stdout);
      this.keep(key, { stdout, at: generation === this.generation ? this.now() : 0 });
      for (const listener of this.listeners) listener(key);
      return view;
    })().finally(() => { if (this.pending.get(key) === promise) this.pending.delete(key); });
    this.pending.set(key, promise);
    return promise;
  }

  /** Keep a list another surface read with the same call (the Stage models panel). */
  put(key: string, stdout: string): void {
    try { readStages(stdout); } catch { return; }
    this.keep(key, { stdout, at: this.now() });
    for (const listener of this.listeners) listener(key);
  }

  /** Mark every list stale, after a change the lists do not show yet (a saved stage choice). */
  expire(): void {
    this.generation++;
    for (const entry of this.entries.values()) entry.at = 0;
    for (const listener of this.listeners) listener("");
  }

  /** Called with a key when its list was read again, or with "" when every list went stale. */
  onChange(listener: (key: string) => void): { dispose(): void } {
    this.listeners.add(listener);
    return { dispose: () => { this.listeners.delete(listener); } };
  }

  private keep(key: string, entry: Entry): void {
    // A Map keeps insertion order: written again, a list moves last, so the oldest goes first.
    this.entries.delete(key);
    this.entries.set(key, entry);
    while (this.entries.size > MAX_ENTRIES) this.entries.delete(this.entries.keys().next().value!);
    void this.save();
  }

  private async save(): Promise<void> {
    if (!this.store) return;
    try { await this.store.update(STORAGE_KEY, Object.fromEntries(this.entries)); } catch { /* the list is shown either way */ }
  }
}
