from __future__ import annotations

from typing import Any

from . import config


# ---------------------------------------------------------------------------
# Cache (file-backed)
# ---------------------------------------------------------------------------
_ENTITY_KEYS = [
    "instrument", "account", "tag", "merchant",
    "transaction", "budget", "reminder", "reminderMarker",
    "user", "country", "company",
]
# Keys whose entities have numeric ids
_NUMERIC_ID_KEYS = {"instrument", "user", "country", "company"}


class Cache:
    """File-backed ZenMoney entity cache with incremental sync."""

    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.server_timestamp: int = 0
        self._base_server_timestamp: int = 0
        # entity_name -> {id_str: entity_dict}
        self.data: dict[str, dict[str, Any]] = {k: {} for k in _ENTITY_KEYS}
        self.data["deletion"] = {}
        self._tags_by_id_cache: dict[str, dict[str, Any]] | None = None

    # -- persistence --------------------------------------------------------

    def load(self) -> None:
        with config.state_file_lock(config.CACHE_PATH):
            self._reset()
            raw = config.read_json_state(config.CACHE_PATH)
            self.server_timestamp = int(raw.get("serverTimestamp", 0) or 0)
            self._base_server_timestamp = self.server_timestamp
            for key in _ENTITY_KEYS:
                arr = raw.get(key, [])
                if isinstance(arr, dict):
                    arr = list(arr.values())
                elif not isinstance(arr, list):
                    arr = []
                store: dict[str, Any] = {}
                for item in arr:
                    if not isinstance(item, dict):
                        continue
                    if key == "budget":
                        bk = self._budget_key(item)
                        store[bk] = item
                    else:
                        store[str(item.get("id", ""))] = item
                self.data[key] = store
            self._invalidate_tag_indexes()

    def save(self) -> None:
        out: dict[str, Any] = {"serverTimestamp": self.server_timestamp}
        for key in _ENTITY_KEYS:
            out[key] = list(self.data[key].values())
        with config.state_file_lock(config.CACHE_PATH):
            disk = config.read_json_state(config.CACHE_PATH)
            disk_timestamp = int(disk.get("serverTimestamp", 0) or 0)
            if disk_timestamp != self._base_server_timestamp:
                raise config.LostUpdateError(
                    config.CACHE_PATH,
                    self.server_timestamp,
                    disk_timestamp,
                    self._base_server_timestamp,
                )
            config.write_json_state_atomic(config.CACHE_PATH, out)
            self._base_server_timestamp = self.server_timestamp

    # -- apply diff ---------------------------------------------------------

    def apply_diff(self, diff: dict[str, Any]) -> None:
        tags_changed = False
        prepared_budget = None
        if diff.get("budget"):
            prepared_budget = self._store_from_items("budget", diff["budget"])
        self._apply_server_timestamp(diff)
        for key in _ENTITY_KEYS:
            items = diff.get(key)
            if not items:
                continue
            if key == "tag":
                tags_changed = True
            store = prepared_budget if key == "budget" else self._store_from_items(key, items)
            self.data[key].update(store or {})
        # deletions
        tags_changed = self._apply_deletions(diff, tags_changed)
        if tags_changed:
            self._invalidate_tag_indexes()

    def apply_force_fetch_diff(self, diff: dict[str, Any], entity_types: list[str] | set[str] | tuple[str, ...]) -> None:
        tags_changed = False
        replacement_types = {entity_type for entity_type in entity_types if entity_type in _ENTITY_KEYS}
        budget_items = diff.get("budget", [])
        prepared_budget = None
        if "budget" in replacement_types or budget_items:
            prepared_budget = self._store_from_items("budget", budget_items)
        self._apply_server_timestamp(diff)
        for key in replacement_types:
            if key == "tag":
                tags_changed = True
            self.data[key] = (
                prepared_budget
                if key == "budget"
                else self._store_from_items(key, diff.get(key, []))
            ) or {}
        for key in _ENTITY_KEYS:
            if key in replacement_types:
                continue
            items = diff.get(key)
            if not items:
                continue
            if key == "tag":
                tags_changed = True
            store = prepared_budget if key == "budget" else self._store_from_items(key, items)
            self.data[key].update(store or {})
        tags_changed = self._apply_deletions(diff, tags_changed)
        if tags_changed:
            self._invalidate_tag_indexes()

    def _apply_server_timestamp(self, diff: dict[str, Any]) -> None:
        if "serverTimestamp" not in diff:
            return
        diff_timestamp = int(diff["serverTimestamp"] or 0)
        if diff_timestamp < self.server_timestamp:
            raise config.LostUpdateError(config.CACHE_PATH, diff_timestamp, self.server_timestamp)
        self.server_timestamp = diff_timestamp

    def _store_from_items(self, key: str, items: Any) -> dict[str, Any]:
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            return {}
        store: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            if key == "budget":
                store[self._budget_key(item)] = item
            else:
                store[str(item.get("id", ""))] = item
        return store

    def _apply_deletions(self, diff: dict[str, Any], tags_changed: bool) -> bool:
        for d in diff.get("deletion", []):
            obj_type = d.get("object", "")
            did = str(d.get("id", ""))
            if obj_type == "tag":
                tags_changed = True
            if obj_type in self.data and did in self.data[obj_type]:
                del self.data[obj_type][did]
        return tags_changed

    @staticmethod
    def _budget_key(b: dict) -> str:
        user = b.get("user")
        if user is None:
            raise ValueError("Budget entity is missing required field: user")
        tag = b.get("tag")
        return (
            f"{'null' if user is None else user}:"
            f"{'null' if tag is None else tag}:"
            f"{b.get('date', '')}"
        )

    # -- helpers ------------------------------------------------------------

    def accounts(self) -> list[dict]:
        return list(self.data["account"].values())

    def transactions(self) -> list[dict]:
        return list(self.data["transaction"].values())

    def tags(self) -> list[dict]:
        return list(self.data["tag"].values())

    def tags_by_id(self) -> dict[str, dict[str, Any]]:
        if self._tags_by_id_cache is None:
            self._tags_by_id_cache = {
                str(tag.get("id", tag_id)): tag
                for tag_id, tag in self.data["tag"].items()
                if isinstance(tag, dict)
            }
        return self._tags_by_id_cache

    def instruments(self) -> list[dict]:
        return list(self.data["instrument"].values())

    def budgets(self) -> list[dict]:
        return list(self.data["budget"].values())

    def reminders(self) -> list[dict]:
        return list(self.data["reminder"].values())

    def reminder_markers(self) -> list[dict]:
        return list(self.data["reminderMarker"].values())

    def merchants(self) -> list[dict]:
        return list(self.data["merchant"].values())

    def users(self) -> list[dict]:
        return list(self.data["user"].values())

    def get(self, entity: str, eid: str) -> dict | None:
        return self.data.get(entity, {}).get(str(eid))

    def get_instrument(self, iid: int | str) -> dict | None:
        return self.data["instrument"].get(str(iid))

    def get_account(self, aid: str) -> dict | None:
        return self.data["account"].get(aid)

    def get_tag(self, tid: str) -> dict | None:
        return self.data["tag"].get(tid)

    def get_merchant(self, mid: str) -> dict | None:
        return self.data["merchant"].get(mid)

    def first_user(self) -> dict | None:
        users = self.users()
        return users[0] if users else None

    def build_category_index(self) -> dict[str, dict]:
        """Build flat category index with parent info from cache data.

        Returns dict mapping category UUID to enriched data:
        - id, title, parent_id, parent_title, is_parent, children_count
        """
        tags = self.tags_by_id()

        # Build parent-child mapping
        children_map: dict[str, list] = {}
        for tag_id, tag in tags.items():
            parent_id = tag.get("parent")
            if parent_id:
                children_map.setdefault(parent_id, []).append(tag_id)

        # Build flat index
        index: dict[str, dict] = {}
        for tag_id, tag in tags.items():
            parent_id = tag.get("parent")
            parent_tag = tags.get(parent_id) if parent_id else None

            index[tag_id] = {
                "id": tag_id,
                "title": tag.get("title", ""),
                "parent_id": parent_id,
                "parent_title": parent_tag.get("title") if parent_tag else None,
                "is_parent": tag_id in children_map,
                "children_count": len(children_map.get(tag_id, [])),
            }

        return index

    def _invalidate_tag_indexes(self) -> None:
        self._tags_by_id_cache = None

    def build_accounts_map(self, accounts_meta: dict) -> dict[str, dict]:
        """Build enriched accounts map from cache data.

        Args:
            accounts_meta: dict mapping account UUID to {"description": str}

        Returns dict mapping account UUID to enriched account data with:
        - id, title, bank, type, subtype, balance, description, etc.
        """
        accounts_map: dict[str, dict] = {}

        for acc_id, acc in self.data.get("account", {}).items():
            company = self.data.get("company", {}).get(str(acc.get("company", "")), {})
            instr = self.data.get("instrument", {}).get(str(acc.get("instrument", "")), {})

            # Determine subtype
            atype = acc.get("type", "")
            credit_limit = acc.get("creditLimit", 0)
            savings = acc.get("savings", False)

            if atype == "ccard" and credit_limit > 0:
                subtype = "credit"
            elif atype == "ccard":
                subtype = "debit"
            elif atype == "checking" and savings:
                subtype = "savings"
            elif atype == "checking":
                subtype = "checking"
            elif atype == "cash":
                subtype = "cash"
            elif atype == "debt":
                subtype = "debt"
            else:
                subtype = atype

            accounts_map[acc_id] = {
                "id": acc_id,
                "title": acc.get("title", ""),
                "bank": company.get("title"),
                "type": atype,
                "subtype": subtype,
                "inBalance": acc.get("inBalance", False),
                "balance": acc.get("balance", 0),
                "creditLimit": credit_limit,
                "currency": instr.get("shortTitle", "?"),
                "savings": savings,
                "archived": acc.get("archive", False),
                "description": accounts_meta.get(acc_id, {}).get("description"),
            }

        return accounts_map


CACHE = Cache()
