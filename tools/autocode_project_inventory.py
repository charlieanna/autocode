"""Conservative regular-file inventories from pinned Git tree listings."""


def regular_entries(inventory, *, executable=False):
    """(path, size) for each regular blob; None when a link, submodule or other mode appears."""
    entries = []
    modes = {"100644", "100755"} if executable else {"100644"}
    for entry in inventory.split("\0"):
        if not entry:
            continue
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 4 or fields[0] not in modes or fields[1] != "blob":
            return None
        entries.append((path, int(fields[3])))
    return entries


def regular_paths(inventory, *, executable=False):
    entries = regular_entries(inventory, executable=executable)
    return None if entries is None else [path for path, _ in entries]
