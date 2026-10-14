"""Conservative regular-file inventories from pinned Git tree listings."""


def regular_paths(inventory, *, executable=False):
    paths = []
    modes = {"100644", "100755"} if executable else {"100644"}
    for entry in inventory.split("\0"):
        if not entry:
            continue
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 4 or fields[0] not in modes or fields[1] != "blob":
            return None
        paths.append(path)
    return paths
