"""
Odczytuje lokalne foldery Foundry VTT zaczynające się od "dnd",
konwertuje ich paczki LevelDB z katalogu packs do plików JSON,
a następnie generuje pliki mapowania Babele/Transifex.

Foldery źródłowe muszą znajdować się w tym samym katalogu co skrypt.
Pliki wynikowe są nazywane prefiksem folderu źródłowego, np.:
dnd-heroes-faerun.actors.json.
"""

import json
import os
import pathlib
import shutil
import zipfile

import plyvel
import requests

CAPTION_ACTOR_MAPPING = {
    "tokenName": {
        "path": "prototypeToken.name",
        "converter": "nested_object_converter"
    },
    "items": {
        "path": "items",
        "converter": "embedded_items_converter"
    },
    "actions": {
        "path": "system.actions",
        "converter": "actions_converter"
    },
    "ancestry": {
        "path": "system.details.ancestry",
        "converter": "embedded_object_with_actions_converter"
    },
    "background": {
        "path": "system.details.background",
        "converter": "embedded_object_with_actions_converter"
    },
    "biography": {
        "path": "system.details.biography",
        "converter": "embedded_biography_converter"
    },
    "archetype": {
        "path": "system.details.archetype",
        "converter": "embedded_object_with_actions_converter"
    },
    "taxonomy": {
        "path": "system.details.taxonomy",
        "converter": "embedded_object_with_actions_converter"
    },
    "alignment": {
        "path": "system.details.alignment",
        "converter": "alignment"
    },
}


def create_version_directory(version: str) -> bool:
    folder_path = pathlib.Path(version).resolve()

    if folder_path.exists():
        print(f'Katalog {version} istnieje, czyszczę jego zawartość.')

        if not folder_path.is_dir():
            print(f"Ścieżka {folder_path} nie jest folderem lub nie istnieje.")
            return False

        for item in folder_path.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
                print(f"Usunięto: {item.name}")
            except Exception as e:
                print(f"Nie udało się usunąć {item.name}: {e}")

        print(f"\nFolder {folder_path.name} jest teraz pusty.")
        return False

    print(f'Tworzę katalog {version}')
    folder_path.mkdir(parents=True, exist_ok=True)
    return True


def download_and_extract_zip(zip_url: str, zip_filename: str, extract_folder_zip: str) -> None:
    response = requests.get(zip_url, timeout=60)
    response.raise_for_status()

    with open(zip_filename, 'wb') as zip_file:
        zip_file.write(response.content)

    with zipfile.ZipFile(zip_filename, 'r') as zip_file:
        zip_file.extractall(extract_folder_zip)

    print('Pobrano i rozpakowano plik .zip')


def read_leveldb_to_json(leveldb_path: str, output_json_path: str) -> None:
    def list_subfolders(directory: str):
        try:
            return [f.name for f in os.scandir(directory) if f.is_dir()]
        except Exception as error:
            raise RuntimeError(f"Wystąpił błąd list_subfolders: {error}") from error

    folders_list = list_subfolders(leveldb_path.replace('\\', '/'))

    for sub_folder in folders_list:
        output_file = os.path.join(output_json_path, f"{sub_folder}.json").replace('\\', '/')
        output_folder = os.path.join(leveldb_path, sub_folder).replace('\\', '/')
        os.makedirs(output_json_path, exist_ok=True)

        db = None
        try:
            db = plyvel.DB(output_folder, create_if_missing=False)
            data = []

            for key, value in db:
                try:
                    value_str = value.decode('utf-8', errors='ignore')
                    try:
                        value_data = json.loads(value_str)
                    except json.JSONDecodeError:
                        value_data = {"name": value_str}

                    data.append(value_data)
                except Exception as e:
                    print(f"Błąd dekodowania dla klucza {key}: {e}")

            with open(output_file, 'w', encoding='utf-8') as json_file:
                json.dump(data, json_file, ensure_ascii=False, indent=4)

            print(f"Dane zostały zapisane do {output_file}")

        except Exception as e:
            raise RuntimeError(f"Wystąpił błąd read_leveldb_to_json dla {output_folder}: {e}") from e
        finally:
            if db is not None:
                db.close()


def sort_entries(input_dict):
    if "entries" in input_dict and isinstance(input_dict["entries"], dict):
        input_dict["entries"] = dict(sorted(input_dict["entries"].items()))

    for key, value in input_dict.items():
        if isinstance(value, dict):
            input_dict[key] = sort_entries(value)

    return input_dict


def remove_empty_keys(data_dict):
    def clean_dict_once(d):
        cleaned = {}
        for key, value in d.items():
            if isinstance(value, dict):
                value = clean_dict_once(value)

            if key == "pages" and not value:
                continue

            if key == "name" and "pages" in d and not d["pages"]:
                continue

            if value not in (None, {}, [], ""):
                cleaned[key] = value

        return cleaned

    previous = None
    current = data_dict

    while previous != current:
        previous = current
        current = clean_dict_once(previous)

    return current


def ensure_actions_mapping(transifex_dict: dict) -> None:
    transifex_dict.setdefault("mapping", {})
    transifex_dict["mapping"]["actions"] = {
        "path": "system.actions",
        "converter": "actions_converter"
    }


def add_actions(entry_dict: dict, new_data: dict, default_name: str, transifex_dict: dict) -> None:
    actions = new_data.get("system", {}).get("actions", [])
    if not actions:
        return

    ensure_actions_mapping(transifex_dict)
    entry_dict.setdefault("actions", {})

    for action in actions:
        action_name = (action.get("name") or default_name).strip()
        entry_dict["actions"].setdefault(action_name, {})
        entry_dict["actions"][action_name]["name"] = action_name
        entry_dict["actions"][action_name]["condition"] = action.get("condition") or ""
        entry_dict["actions"][action_name]["description"] = action.get("description") or ""

        effects = action.get("effects", [])
        if effects:
            entry_dict["actions"][action_name]["effects"] = []

            for effect in effects:
                effect_name = (effect.get("name") or action_name).strip()
                entry_dict["actions"][action_name]["effects"].append({
                    "name": effect_name
                })


def build_id_index(data: list[dict]) -> dict:
    index = {}
    for obj in data:
        if isinstance(obj, dict) and obj.get("_id"):
            index[obj["_id"]] = obj
    return index


def build_records_by_id(data: list[dict], predicate) -> dict[str, list[dict]]:
    """
    Zachowuje wszystkie rekordy o tym samym _id.

    W paczkach Actor dokumenty osadzone, np. Itemy potworów, mogą mieć takie
    samo _id u wielu różnych aktorów. Zwykły indeks {id: record} zachowuje
    tylko ostatni rekord i gubi informację, który wariant należy do aktora.
    """
    records_by_id: dict[str, list[dict]] = {}

    for record in data:
        if not isinstance(record, dict):
            continue

        if not predicate(record):
            continue

        record_id = record.get("_id")
        if not isinstance(record_id, str) or not record_id.strip():
            continue

        records_by_id.setdefault(record_id, []).append(record)

    return records_by_id


def resolve_record_in_actor_order(
        record_id: str,
        id_index: dict,
        records_by_id: dict[str, list[dict]] | None,
        record_positions: dict[str, int] | None
) -> dict | None:
    """
    Rozwiązuje ID rekordu osadzonego z zachowaniem kolejności aktorów.

    LevelDB przechowuje osadzone dokumenty pod kluczami zawierającymi rodzica,
    ale po eksporcie do listy JSON pozostaje tylko wartość dokumentu. Gdy wiele
    osadzonych itemów ma ten sam _id, jedyną dostępną informacją pozwalającą
    odtworzyć przypisanie jest kolejność rekordów w paczce. Dlatego kolejne
    odwołania aktorów do tego samego ID zużywają kolejne rekordy o tym ID.
    """
    if not isinstance(record_id, str) or not record_id.strip():
        return None

    if records_by_id is not None and record_positions is not None:
        records = records_by_id.get(record_id)
        if records:
            position = record_positions.get(record_id, 0)
            if position < len(records):
                record_positions[record_id] = position + 1
                return records[position]

    record = id_index.get(record_id)
    return record if isinstance(record, dict) else None


def extract_description(record: dict) -> str:
    if not isinstance(record, dict):
        return ""

    # 1. Najpierw typowy D&D 5e item/actor embedded description
    system_description = record.get("system", {}).get("description")
    if isinstance(system_description, dict):
        public_desc = (system_description.get("public") or "").strip()
        private_desc = (system_description.get("private") or "").strip()

        if public_desc and private_desc:
            return f"{public_desc}\n\n{private_desc}"
        if public_desc:
            return public_desc
        if private_desc:
            return private_desc

    # 2. Prostsze opisy stringowe
    candidates = [
        record.get("system", {}).get("description", {}).get("value")
        if isinstance(record.get("system", {}).get("description"), dict) else None,
        record.get("system", {}).get("description", {}).get("chat")
        if isinstance(record.get("system", {}).get("description"), dict) else None,
        record.get("system", {}).get("description", {}).get("unidentified")
        if isinstance(record.get("system", {}).get("description"), dict) else None,
        record.get("system", {}).get("description"),
        record.get("description"),
        record.get("system", {}).get("details", {}).get("description"),
    ]

    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()

    # 3. Biography jako fallback, tylko jeśli naprawdę chcesz ją traktować jako opis
    biography = record.get("system", {}).get("details", {}).get("biography")
    if isinstance(biography, dict):
        public_bio = (biography.get("public") or "").strip()
        private_bio = (biography.get("private") or "").strip()

        if public_bio and private_bio:
            return f"{public_bio}\n\n{private_bio}"
        if public_bio:
            return public_bio
        if private_bio:
            return private_bio

    return ""


def extract_description_value(record: dict):
    """
    Zwraca description w oryginalnym formacie:
    - dict {"public": "...", "private": "..."} jeśli źródło ma taki format
    - string jeśli źródło ma zwykły tekst
    - "" jeśli brak opisu
    """
    if not isinstance(record, dict):
        return ""

    system_description = record.get("system", {}).get("description")

    # 1. Format obiektowy: D&D 5e {"value": "...", "chat": "...", "unidentified": "..."}
    # albo D&D 5e-like {"public": "...", "private": "..."}.
    if isinstance(system_description, dict):
        result = {}

        for key in ("value", "chat", "unidentified", "public", "private"):
            value = system_description.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()

        if result:
            return result

    # 2. Format tekstowy
    if isinstance(system_description, str) and system_description.strip():
        return system_description.strip()

    # 3. Fallback na description w korzeniu
    plain_description = record.get("description")

    if isinstance(plain_description, dict):
        result = {}

        public_desc = plain_description.get("public")
        private_desc = plain_description.get("private")

        if isinstance(public_desc, str) and public_desc.strip():
            result["public"] = public_desc.strip()
        if isinstance(private_desc, str) and private_desc.strip():
            result["private"] = private_desc.strip()

        if result:
            return result

    if isinstance(plain_description, str) and plain_description.strip():
        return plain_description.strip()

    return ""


def extract_description_text(record: dict) -> str:
    """
    Zwraca opis jako tekst tam, gdzie wynik ma być płaski.
    """
    description_value = extract_description_value(record)

    if isinstance(description_value, str):
        return description_value

    if isinstance(description_value, dict):
        public_desc = (description_value.get("public") or "").strip()
        private_desc = (description_value.get("private") or "").strip()

        if public_desc and private_desc:
            return f"{public_desc}\n\n{private_desc}"
        if public_desc:
            return public_desc
        if private_desc:
            return private_desc

    details_description = record.get("system", {}).get("details", {}).get("description")
    if isinstance(details_description, str) and details_description.strip():
        return details_description.strip()

    biography = record.get("system", {}).get("details", {}).get("biography")
    if isinstance(biography, dict):
        public_bio = (biography.get("public") or "").strip()
        private_bio = (biography.get("private") or "").strip()

        if public_bio and private_bio:
            return f"{public_bio}\n\n{private_bio}"
        if public_bio:
            return public_bio
        if private_bio:
            return private_bio

    return ""


def ensure_nested_mapping(transifex_dict: dict, key: str, path: str, converter: str) -> None:
    transifex_dict.setdefault("mapping", {})
    transifex_dict["mapping"][key] = {
        "path": path,
        "converter": converter
    }


def add_actions_from_record(
        target_entry: dict,
        source_record: dict,
        fallback_name: str,
        transifex_dict: dict,
        add_mapping: bool = True
) -> None:
    actions = source_record.get("system", {}).get("actions", [])
    if not actions:
        return

    if add_mapping:
        ensure_actions_mapping(transifex_dict)
    target_entry.setdefault("actions", {})

    for action in actions:
        action_name = (action.get("name") or fallback_name).strip()
        if not action_name:
            continue

        target_entry["actions"].setdefault(action_name, {})
        target_entry["actions"][action_name]["name"] = action_name
        target_entry["actions"][action_name]["condition"] = action.get("condition") or ""
        target_entry["actions"][action_name]["description"] = action.get("description") or ""

        effects = action.get("effects", [])
        if effects:
            target_entry["actions"][action_name]["effects"] = []
            for effect in effects:
                effect_name = (effect.get("name") or action_name).strip()
                target_entry["actions"][action_name]["effects"].append({
                    "name": effect_name
                })


def resolve_reference(ref_id: str, id_index: dict) -> dict | None:
    if not ref_id or not isinstance(ref_id, str):
        return None
    return id_index.get(ref_id)


def resolve_reference_list(ref_list, id_index: dict) -> list[dict]:
    resolved = []
    if isinstance(ref_list, list):
        for ref_id in ref_list:
            record = resolve_reference(ref_id, id_index)
            if record:
                resolved.append(record)
    elif isinstance(ref_list, str):
        record = resolve_reference(ref_list, id_index)
        if record:
            resolved.append(record)
    return resolved


def fill_translated_object_from_record(
        target_obj: dict,
        source_record: dict,
        transifex_dict: dict,
        preserve_description_shape: bool = False
) -> None:
    source_name = (source_record.get("name") or "").strip()
    if source_name:
        target_obj["name"] = source_name

    if preserve_description_shape:
        description_value = extract_description_value(source_record)
        if description_value not in ("", {}, None):
            target_obj["description"] = description_value
    else:
        description_text = extract_description_text(source_record)
        if description_text:
            target_obj["description"] = description_text

    add_actions_from_record(
        target_entry=target_obj,
        source_record=source_record,
        fallback_name=source_name or "action",
        transifex_dict=transifex_dict
    )


def populate_reference_bucket(
        parent_entry: dict,
        bucket_name: str,
        source_value,
        id_index: dict,
        transifex_dict: dict
) -> None:
    resolved_records = resolve_reference_list(source_value, id_index)
    if not resolved_records:
        return

    parent_entry.setdefault(bucket_name, {})

    for record in resolved_records:
        record_name = (record.get("name") or "").strip()
        if not record_name:
            continue

        parent_entry[bucket_name].setdefault(record_name, {})

        # Dla items zachowujemy oryginalny format description:
        # string albo {"public", "private"}
        preserve_description_shape = bucket_name == "items"

        fill_translated_object_from_record(
            target_obj=parent_entry[bucket_name][record_name],
            source_record=record,
            transifex_dict=transifex_dict,
            preserve_description_shape=preserve_description_shape
        )


def populate_single_reference_object(
        parent_entry: dict,
        field_name: str,
        source_value,
        id_index: dict,
        transifex_dict: dict
) -> None:
    record = None

    if isinstance(source_value, str):
        record = resolve_reference(source_value, id_index)
    elif isinstance(source_value, dict) and source_value.get("_id"):
        record = resolve_reference(source_value["_id"], id_index)

    if not record:
        return

    parent_entry.setdefault(field_name, {})
    fill_translated_object_from_record(
        target_obj=parent_entry[field_name],
        source_record=record,
        transifex_dict=transifex_dict
    )


def extract_alignment(record: dict) -> str:
    """
    Pobiera surową wartość system.details.alignment.
    Nie ogranicza się do monsters; działa dla każdego dokumentu aktora,
    również w actors24, heroes oraz aktorach osadzonych w przygodach/caption.
    """
    alignment = record.get("system", {}).get("details", {}).get("alignment")
    if isinstance(alignment, str) and alignment.strip():
        return alignment.strip()
    return ""


def populate_prototype_fields(
        entry: dict,
        new_data: dict,
        id_index: dict,
        transifex_dict: dict,
        items_source=None
) -> None:
    mapping_data = {
        "items": ("items", "adventure_items_converter"),
        "actions": ("system.actions", "actions_converter"),
        "ancestry": ("system.details.ancestry", "nested_object_converter"),
        "background": ("system.details.background", "nested_object_converter"),
        "biography": ("system.details.biography", "nested_object_converter"),
        "archetype": ("system.details.archetype", "nested_object_converter"),
        "taxonomy": ("system.details.taxonomy", "nested_object_converter"),
        "alignment": ("system.details.alignment", "alignment"),
    }

    transifex_dict.setdefault("mapping", {})
    for key, (path, conv) in mapping_data.items():
        transifex_dict["mapping"][key] = {
            "path": path,
            "converter": conv
        }

    # actions bezpośrednio na rekordzie
    add_actions_from_record(
        target_entry=entry,
        source_record=new_data,
        fallback_name=entry.get("name", "action"),
        transifex_dict=transifex_dict
    )

    # items: źródło zależne od typu danych
    if items_source is None:
        items_source = new_data.get("items", [])

    populate_reference_bucket(
        parent_entry=entry,
        bucket_name="items",
        source_value=items_source,
        id_index=id_index,
        transifex_dict=transifex_dict
    )

    # ancestry/background/biography/archetype/taxonomy
    details = new_data.get("system", {}).get("details", {})

    alignment = extract_alignment(new_data)
    if alignment:
        entry["alignment"] = alignment

    for field_name in ["ancestry", "background", "biography", "archetype", "taxonomy"]:
        source_value = details.get(field_name)

        record = None
        if isinstance(source_value, str):
            record = resolve_reference(source_value, id_index)
        elif isinstance(source_value, dict):
            if source_value.get("_id"):
                record = resolve_reference(source_value["_id"], id_index)
            else:
                record = source_value

        if not record:
            continue

        entry.setdefault(field_name, {})
        fill_translated_object_from_record(
            target_obj=entry[field_name],
            source_record=record,
            transifex_dict=transifex_dict
        )


def populate_actor_like_prototype(
        actor_entry: dict,
        actor_data: dict,
        id_index: dict,
        transifex_dict: dict
) -> None:
    actor_name = (actor_data.get("name") or "").strip()
    if actor_name:
        actor_entry["name"] = actor_name

    prototype = actor_data.get("prototypeToken", {})
    token_name = prototype.get("name")
    if token_name not in (None, ""):
        actor_entry["tokenName"] = {"name": token_name}

    populate_prototype_fields(
        entry=actor_entry,
        new_data=actor_data,
        id_index=id_index,
        transifex_dict=transifex_dict,
        items_source=prototype.get("items", [])
    )


def ensure_caption_actor_mapping(transifex_dict: dict) -> None:
    transifex_dict.setdefault("mapping", {})
    transifex_dict["mapping"]["actors"] = {
        "path": "actors",
        "converter": "document",
        "documentType": "Actor",
        "cardinality": "many",
        "mapping": {
            "tokenName": {
                "path": "prototypeToken.name",
                "converter": "name"
            },
            "items": {
                "path": "items",
                "converter": "embedded_items_converter"
            },
            "actions": {
                "path": "system.actions",
                "converter": "actions_converter"
            },
            "ancestry": {
                "path": "system.details.ancestry",
                "converter": "embedded_object_with_actions_converter"
            },
            "background": {
                "path": "system.details.background",
                "converter": "embedded_object_with_actions_converter"
            },
            "archetype": {
                "path": "system.details.archetype",
                "converter": "embedded_object_with_actions_converter"
            },
            "taxonomy": {
                "path": "system.details.taxonomy",
                "converter": "embedded_object_with_actions_converter"
            },
            "biography": {
                "path": "system.details.biography",
                "converter": "embedded_biography_converter"
            },
            "alignment": {
                "path": "system.details.alignment",
                "converter": "alignment"
            },
        }
    }


def populate_caption_actor(
        actor_entry: dict,
        actor_data: dict,
        transifex_dict: dict
) -> None:
    actor_name = (actor_data.get("name") or "").strip()
    if actor_name:
        actor_entry["name"] = actor_name

    ensure_caption_actor_mapping(transifex_dict)

    prototype = actor_data.get("prototypeToken", {})
    token_name = prototype.get("name")
    if isinstance(token_name, str) and token_name.strip():
        actor_entry["tokenName"] = token_name.strip()

    transifex_dict["mapping"]["alignment"] = {
        "path": "system.details.alignment",
        "converter": "alignment"
    }

    # actions bezpośrednio na aktorze
    add_actions_from_record(
        target_entry=actor_entry,
        source_record=actor_data,
        fallback_name=actor_name or "action",
        transifex_dict=transifex_dict,
        add_mapping=False
    )

    # items są osadzone bezpośrednio w actor_data["items"]
    items = actor_data.get("items", [])
    if items and isinstance(items, list):
        actor_entry.setdefault("items", {})
        ensure_caption_actor_mapping(transifex_dict)

        for item in items:
            if not isinstance(item, dict):
                continue

            item_name = (item.get("name") or "").strip()
            if not item_name:
                continue

            actor_entry["items"].setdefault(item_name, {})
            actor_entry["items"][item_name]["name"] = item_name

            item_description = extract_description_value(item)
            if item_description not in ("", {}, None):
                actor_entry["items"][item_name]["description"] = item_description

            add_actions_from_record(
                target_entry=actor_entry["items"][item_name],
                source_record=item,
                fallback_name=item_name,
                transifex_dict=transifex_dict,
                add_mapping=False
            )

    details = actor_data.get("system", {}).get("details", {})

    for field_name in ["ancestry", "background", "archetype", "taxonomy"]:
        obj = details.get(field_name)
        if not isinstance(obj, dict):
            continue

        actor_entry.setdefault(field_name, {})

        obj_name = (obj.get("name") or "").strip()
        if obj_name:
            actor_entry[field_name]["name"] = obj_name

        obj_description = extract_description(obj)
        if obj_description:
            actor_entry[field_name]["description"] = obj_description

        add_actions_from_record(
            target_entry=actor_entry[field_name],
            source_record=obj,
            fallback_name=obj_name or field_name,
            transifex_dict=transifex_dict,
            add_mapping=False
        )

    biography = details.get("biography")
    if isinstance(biography, dict):
        actor_entry.setdefault("biography", {})
        for key, value in biography.items():
            if isinstance(value, str) and value.strip():
                actor_entry["biography"][key] = value.strip()


def ensure_items_mapping_for_caption(transifex_dict: dict) -> None:
    transifex_dict.setdefault("mapping", {})
    transifex_dict["mapping"]["items"] = {
        "path": "items",
        "converter": "embedded_items_converter"
    }

    transifex_dict["mapping"]["actions"] = {
        "path": "system.actions",
        "converter": "actions_converter"
    }

    for key in ["ancestry", "background", "archetype", "taxonomy"]:
        transifex_dict["mapping"][key] = {
            "path": f"system.details.{key}",
            "converter": "embedded_object_with_actions_converter"
        }

    transifex_dict["mapping"]["biography"] = {
        "path": "system.details.biography",
        "converter": "embedded_biography_converter"
    }

    transifex_dict["mapping"]["tokenName"] = {
        "path": "prototypeToken.name",
        "converter": "nested_object_converter"
    }


def populate_caption_entry(entry: dict, new_data: dict, id_index: dict, transifex_dict: dict) -> None:
    entry["caption"] = new_data.get("caption", "")
    entry["description"] = new_data.get("description", "")

    # Foldery
    if "folders" in new_data and isinstance(new_data["folders"], list):
        entry.setdefault("folders", {})
        for folder in new_data["folders"]:
            folder_name = (folder.get("name") or "").strip()
            if folder_name:
                entry["folders"][folder_name] = folder_name

    # Dzienniki
    if "journal" in new_data and isinstance(new_data["journal"], list):
        entry.setdefault("journals", {})
        for journal in new_data["journal"]:
            journal_name = (journal.get("name") or "").strip()
            if not journal_name:
                continue

            entry["journals"].setdefault(journal_name, {})
            entry["journals"][journal_name]["name"] = journal_name
            entry["journals"][journal_name].setdefault("pages", {})

            for page in journal.get("pages", []):
                page_name = (page.get("name") or "").strip()
                if not page_name:
                    continue

                entry["journals"][journal_name]["pages"].setdefault(page_name, {})
                entry["journals"][journal_name]["pages"][page_name]["name"] = page_name
                entry["journals"][journal_name]["pages"][page_name]["text"] = (
                    " ".join(page.get("text", {}).get("content", "").split())
                )

    # Sceny
    if "scenes" in new_data and isinstance(new_data["scenes"], list):
        entry.setdefault("scenes", {})
        for scene in new_data["scenes"]:
            scene_name = (scene.get("name") or "").strip()
            if not scene_name:
                continue

            entry["scenes"].setdefault(scene_name, {})
            entry["scenes"][scene_name]["name"] = scene_name
            entry["scenes"][scene_name].setdefault("notes", {})

            for note in scene.get("notes", []):
                note_text = (note.get("text") or "").strip()
                if note_text:
                    entry["scenes"][scene_name]["notes"][note_text] = note_text

    # Makra
    if "macros" in new_data and isinstance(new_data["macros"], list):
        entry.setdefault("macros", {})
        for macro in new_data["macros"]:
            macro_name = (macro.get("name") or "").strip()
            if not macro_name:
                continue

            entry["macros"].setdefault(macro_name, {})
            entry["macros"][macro_name]["name"] = macro_name

            # if macro.get("command") not in (None, ""):
            #     entry["macros"][macro_name]["command"] = macro.get("command")

    # Tabele
    if "tables" in new_data and isinstance(new_data["tables"], list):
        entry.setdefault("tables", {})
        for table in new_data["tables"]:
            table_name = (table.get("name") or "").strip()
            if not table_name:
                continue

            entry["tables"].setdefault(table_name, {})
            entry["tables"][table_name]["name"] = table_name
            entry["tables"][table_name]["description"] = table.get("description", "")
            entry["tables"][table_name].setdefault("results", {})

            for result in table.get("results", []):
                range_data = result.get("range", [])
                if isinstance(range_data, list) and len(range_data) >= 2:
                    result_name = f'{range_data[0]}-{range_data[1]}'
                else:
                    result_name = "unknown"

                entry["tables"][table_name]["results"][result_name] = result.get("text", "")

    # Przedmioty
    if "items" in new_data and isinstance(new_data["items"], list):
        entry.setdefault("items", {})
        for item in new_data["items"]:
            item_name = (item.get("name") or "").strip()
            if not item_name:
                continue

            entry["items"].setdefault(item_name, {})
            entry["items"][item_name]["name"] = item_name

    # Playlisty
    if "playlists" in new_data and isinstance(new_data["playlists"], list):
        entry.setdefault("playlists", {})
        for playlist in new_data["playlists"]:
            playlist_name = (playlist.get("name") or "").strip()
            if not playlist_name:
                continue

            entry["playlists"].setdefault(playlist_name, {})
            entry["playlists"][playlist_name]["name"] = playlist_name
            entry["playlists"][playlist_name]["description"] = playlist.get("description")
            entry["playlists"][playlist_name].setdefault("sounds", {})

            for sound in playlist.get("sounds", []):
                sound_name = (sound.get("name") or "").strip()
                if not sound_name:
                    continue

                entry["playlists"][playlist_name]["sounds"].setdefault(sound_name, {})
                entry["playlists"][playlist_name]["sounds"][sound_name]["name"] = sound_name
                entry["playlists"][playlist_name]["sounds"][sound_name]["description"] = sound.get("description")

    # Aktorzy
    if "actors" in new_data and isinstance(new_data["actors"], list):
        entry.setdefault("actors", {})
        for actor in new_data["actors"]:
            actor_name = (actor.get("name") or "").strip()
            if not actor_name:
                continue

            entry["actors"].setdefault(actor_name, {})
            populate_caption_actor(
                actor_entry=entry["actors"][actor_name],
                actor_data=actor,
                transifex_dict=transifex_dict
            )


def ensure_rules_mapping(transifex_dict: dict) -> None:
    transifex_dict.setdefault("mapping", {})
    transifex_dict["mapping"]["categories"] = {
        "path": "categories",
        "converter": "categories_converter"
    }


def populate_rules_entry(entry: dict, new_data: dict, id_index: dict, transifex_dict: dict) -> None:
    ensure_rules_mapping(transifex_dict)

    entry["name"] = (new_data.get("name") or "").strip()

    # Categories: kluczowane po _id, jak w D&D 5e-FR
    category_ids = new_data.get("categories", [])
    if isinstance(category_ids, list) and category_ids:
        entry.setdefault("categories", {})
        for category_id in category_ids:
            category_obj = id_index.get(category_id)
            if not isinstance(category_obj, dict):
                continue

            category_name = (category_obj.get("name") or "").strip()
            if not category_name:
                continue

            entry["categories"].setdefault(category_id, {})
            entry["categories"][category_id]["name"] = category_name

    # Pages: kluczowane po nazwie strony
    page_ids = new_data.get("pages", [])
    if isinstance(page_ids, list) and page_ids:
        entry.setdefault("pages", {})
        for page_id in page_ids:
            page_obj = id_index.get(page_id)
            if not isinstance(page_obj, dict):
                continue

            page_name = (page_obj.get("name") or "").strip()
            if not page_name:
                continue

            text_content = page_obj.get("text", {}).get("content", "")
            if not isinstance(text_content, str):
                text_content = ""

            entry["pages"].setdefault(page_name, {})
            entry["pages"][page_name]["name"] = page_name
            entry["pages"][page_name]["text"] = text_content


TABLE_LABEL_OVERRIDES = {
    "tables": "Tables (SRD)",
    "tables24": "Roll Tables",
}

JOURNAL_LABEL_OVERRIDES = {
    "rules": "Rules (SRD)",
    "content": "Content",
    "content24": "Rules",
}


def looks_like_journal_pack(data: list[dict]) -> bool:
    """
    Rozpoznaje paczkę JournalEntry niezależnie od jej nazwy.

    Obsługuje między innymi:
    - rules
    - content
    - content24
    - book
    - inne paczki zawierające JournalEntry z listą pages
    """
    if not isinstance(data, list):
        return False

    return any(
        isinstance(record, dict)
        and isinstance(record.get("pages"), list)
        and isinstance(record.get("name"), str)
        and bool(record["name"].strip())
        for record in data
    )


def is_folder_record(record: dict) -> bool:
    """
    Rozpoznaje rekord folderu w eksporcie LevelDB Foundry.

    W D&D 5e foldery w paczkach nie zawsze mają type == "Folder".
    Często mają type takie jak dokument w paczce, np. "Item" albo "RollTable",
    ale nie mają właściwego payloadu dokumentu, czyli np. system, results albo pages.
    """
    if not isinstance(record, dict):
        return False

    record_type = record.get("type")

    if record_type in {"Folder", "folder"}:
        return True

    has_document_payload = any(
        key in record
        for key in (
            "system",
            "results",
            "pages",
            "text",
            "prototypeToken",
            "items",
            "actors",
            "journal",
            "caption",
            "scenes",
            "tables",
            "playlists",
            "macros",
        )
    )

    if has_document_payload:
        return False

    return (
            isinstance(record.get("_id"), str)
            and isinstance(record.get("name"), str)
            and "sorting" in record
            and "folder" in record
    )


def is_rolltable_folder(record: dict) -> bool:
    """
    Foldery tabel w paczce tables/tables24 często mają type == "RollTable",
    ale NIE mają pola results. Właściwe tabele mają results.
    """
    if not isinstance(record, dict):
        return False

    return (
            record.get("type") == "RollTable"
            and "results" not in record
            and (
                    "sorting" in record
                    or "color" in record
            )
    )


def is_rolltable_record(record: dict) -> bool:
    """
    Właściwy dokument RollTable ma listę results.
    """
    return (
            isinstance(record, dict)
            and isinstance(record.get("results"), list)
            and (record.get("name") or "").strip()
    )


def is_table_result_record(record: dict) -> bool:
    """
    Rekord TableResult zwykle ma _id, range i description/text.
    W output D&D5e nie zawsze ma jawne type == "TableResult", więc nie można
    sprawdzać tylko pola type.
    """
    if not isinstance(record, dict):
        return False

    return (
            isinstance(record.get("range"), list)
            and len(record.get("range")) >= 2
            and (
                    "description" in record
                    or "text" in record
                    or "documentUuid" in record
                    or "documentCollection" in record
            )
    )


def extract_plain_description(record: dict) -> str:
    """
    Zwraca surowy opis bez konwersji HTML.
    Nie usuwa tagów, nie dekoduje encji HTML i nie robi plain-text.
    """
    if not isinstance(record, dict):
        return ""

    root_description = record.get("description")
    if isinstance(root_description, str) and root_description.strip():
        return root_description.strip()

    if isinstance(root_description, dict):
        for key in ("value", "public", "private", "chat", "unidentified"):
            value = root_description.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    system_description = record.get("system", {}).get("description")
    if isinstance(system_description, str) and system_description.strip():
        return system_description.strip()

    if isinstance(system_description, dict):
        for key in ("value", "public", "private", "chat", "unidentified"):
            value = system_description.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def table_result_range_key(result_record: dict) -> str | None:
    """
    Zamienia range z TableResult na klucz Babele, np.
    [1, 1] -> "1-1"
    [2, 10] -> "2-10"
    """
    range_data = result_record.get("range")

    if not isinstance(range_data, list) or len(range_data) < 2:
        return None

    start = range_data[0]
    end = range_data[1]

    if start is None or end is None:
        return None

    return f"{start}-{end}"


def extract_table_result_description(result_record: dict) -> str:
    """
    Pobiera opis wyniku tabeli jako surowy string.

    Priorytet:
    1. description
    2. text
    3. @UUID[...] z dokumentu, jeśli wynik wskazuje na dokument
    """
    description = result_record.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()

    text = result_record.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()

    document_uuid = result_record.get("documentUuid")
    result_name = (result_record.get("name") or "").strip()

    if isinstance(document_uuid, str) and document_uuid.strip():
        if result_name:
            return f"@UUID[{document_uuid}]{{{result_name}}}"
        return f"@UUID[{document_uuid}]"

    return ""


def extract_rolltable_results(table_record: dict, id_index: dict) -> dict:
    """
    Rozwiązuje RollTable.results z listy ID na strukturę Babele:

    "results": {
        "1-1": {
            "description": "..."
        }
    }
    """
    output_results = {}

    result_refs = table_record.get("results")
    if not isinstance(result_refs, list):
        return output_results

    for result_ref in result_refs:
        result_record = None

        if isinstance(result_ref, str):
            result_record = id_index.get(result_ref)
        elif isinstance(result_ref, dict):
            result_record = result_ref

        if not isinstance(result_record, dict):
            continue

        range_key = table_result_range_key(result_record)
        if not range_key:
            continue

        description = extract_table_result_description(result_record)
        if not description:
            continue

        output_results[range_key] = {
            "description": description
        }

    return output_results


def process_table_pack(data: list[dict], pack_name: str) -> dict:
    """
    Eksport tabel zgodny z Babele i francuskim compendium_en.

    RollTable:
    entries[tableName].name
    entries[tableName].description
    entries[tableName].results[range].description

    TableResult z własną nazwą może też zostać osobnym entry, np.
    "Blue", "Green", "Special", bo tak występuje w referencyjnym tables24.
    """
    folders = collect_folder_names(data)
    id_index = build_id_index(data)

    transifex_dict = {
        "label": TABLE_LABEL_OVERRIDES.get(pack_name, pack_name.title()),
        "entries": {},
    }

    folders: dict[str, str] = {}

    for record in data:
        if not isinstance(record, dict):
            continue

        name = (record.get("name") or "").strip()

        if is_folder_record(record):
            continue

        if is_rolltable_record(record):
            if not name:
                continue

            entry = {"name": name}

            description = extract_plain_description(record)
            if description:
                entry["description"] = description

            results = extract_rolltable_results(record, id_index)
            if results:
                entry["results"] = dict(
                    sorted(
                        results.items(),
                        key=lambda item: [
                            int(part) if str(part).isdigit() else str(part)
                            for part in item[0].split("-")
                        ]
                    )
                )

            transifex_dict["entries"][name] = entry
            continue

        # Nazwane TableResult zostają osobnymi entries, bo w tables24 są np.
        # Blue, Green, Red, Special, Violet itd.
        if is_table_result_record(record) and name:
            entry = {"name": name}

            description = extract_table_result_description(record)
            if description:
                entry["description"] = description

            transifex_dict["entries"][name] = entry
            continue

    if folders:
        transifex_dict["folders"] = folders

    transifex_dict["entries"] = dict(
        sorted(transifex_dict["entries"].items(), key=lambda item: item[0].casefold())
    )

    return remove_empty_keys(transifex_dict)


def extract_plain_description(record: dict) -> str:
    """
    Zwraca opis jako pojedynczy tekst dla plików wynikowych, które nie używają
    strukturalnego mapowania description. Dla tabel jest to root-level
    description, a dla innych dokumentów fallback do typowych pól D&D 5e.
    """
    if not isinstance(record, dict):
        return ""

    root_description = record.get("description")
    if isinstance(root_description, str) and root_description.strip():
        return root_description.strip()

    if isinstance(root_description, dict):
        for key in ("value", "public", "private", "chat", "unidentified"):
            value = root_description.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    system_description = record.get("system", {}).get("description")
    if isinstance(system_description, str) and system_description.strip():
        return system_description.strip()

    if isinstance(system_description, dict):
        for key in ("value", "public", "private", "chat", "unidentified"):
            value = system_description.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def extract_journal_page_text(page: dict) -> str:
    """
    Pobiera standardową treść JournalEntryPage.

    Według domyślnego mapowania Babele:
        text -> text.content

    Pole to jest niezależne od system.description.value.
    Jeżeli strona ma oba pola, oba zostaną zapisane.
    """
    if not isinstance(page, dict):
        return ""

    text_value = page.get("text")

    if isinstance(text_value, dict):
        content = text_value.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()

    elif isinstance(text_value, str) and text_value.strip():
        return text_value.strip()

    # Fallback dla starszych lub niestandardowych struktur.
    content = page.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    return ""


def extract_journal_page_descriptions(page: dict) -> dict:
    """
    Pobiera dodatkowe pola tekstowe z system.description strony dziennika.

    system.description.value jest eksportowane jako description.

    Pozostałe pola tekstowe zachowują swoją nazwę, np.:
        system.description.subclass -> subclass
        system.description.additionalHitPoints -> additionalHitPoints
    """
    if not isinstance(page, dict):
        return {}

    system = page.get("system")
    if not isinstance(system, dict):
        return {}

    description = system.get("description")
    if not isinstance(description, dict):
        return {}

    result = {}

    for source_key, value in description.items():
        if not isinstance(value, str):
            continue

        value = value.strip()
        if not value:
            continue

        output_key = "description" if source_key == "value" else source_key
        result[output_key] = value

    return result


def build_journal_pages_mapping(data: list[dict]) -> dict:
    """
    Dodaje pola specyficzne dla stron dziennika D&D 5e Premium.

    Pola name, text, caption, src, width i height są już obsługiwane
    przez domyślne mapowanie Babele.
    """
    page_mapping: dict[str, str] = {}

    for record in data:
        if not isinstance(record, dict):
            continue

        # JournalEntry ma listę pages. Interesują nas rekordy JournalEntryPage.
        if isinstance(record.get("pages"), list):
            continue

        description = record.get("system", {}).get("description")

        if isinstance(description, dict):
            for source_key, value in description.items():
                if not isinstance(value, str) or not value.strip():
                    continue

                output_key = (
                    "description"
                    if source_key == "value"
                    else source_key
                )

                page_mapping[output_key] = (
                    f"system.description.{source_key}"
                )

        dnd5e_title = (
            record.get("flags", {})
            .get("dnd5e", {})
            .get("title")
        )

        if isinstance(dnd5e_title, str) and dnd5e_title.strip():
            page_mapping["chapterTitle"] = "flags.dnd5e.title"

    pages_mapping = {
        "path": "pages",
        "converter": "document",
        "documentType": "JournalEntryPage",
        "cardinality": "many"
    }

    if page_mapping:
        pages_mapping["mapping"] = dict(
            sorted(
                page_mapping.items(),
                key=lambda item: item[0].casefold()
            )
        )

    return pages_mapping


def resolve_journal_pages(pages_value, id_index: dict) -> list[dict]:
    pages = []
    if not isinstance(pages_value, list):
        return pages

    for page in pages_value:
        if isinstance(page, dict):
            pages.append(page)
        elif isinstance(page, str):
            resolved = id_index.get(page)
            if isinstance(resolved, dict):
                pages.append(resolved)

    return pages


def resolve_journal_categories(
        categories_value,
        id_index: dict
) -> dict[str, str]:
    """
    Odtwarza JournalEntry.categories w formacie eksportu Babele.

    Foundry przechowuje kategorie jako listę identyfikatorów.
    Babele eksportuje je jako:
        {"Nazwa kategorii": "Nazwa kategorii"}
    """
    if not isinstance(categories_value, list):
        return {}

    categories: dict[str, str] = {}

    for category_value in categories_value:
        category_record = None

        if isinstance(category_value, str):
            category_record = id_index.get(category_value)
        elif isinstance(category_value, dict):
            category_record = category_value

        if not isinstance(category_record, dict):
            continue

        category_name = (
                category_record.get("name") or ""
        ).strip()

        if category_name:
            categories[category_name] = category_name

    return categories


def process_rules_pack(
        data: list[dict],
        pack_name: str,
        pack_label: str | None = None
) -> dict:
    """
    Eksportuje paczkę JournalEntry w kształcie zgodnym z eksportem Babele.

    Zachowuje standardowe pola Babele i dodaje pola specyficzne dla D&D 5e,
    których domyślne mapowanie Babele nie obejmuje:
        - flags.dnd5e.title na JournalEntry,
        - flags.dnd5e.title na JournalEntryPage,
        - system.description.* na JournalEntryPage.
    """
    id_index = build_id_index(data)
    folders = collect_journal_folder_names(data)

    mapping = {
        "pages": build_journal_pages_mapping(data)
    }

    has_entry_title = any(
        isinstance(record, dict)
        and isinstance(record.get("pages"), list)
        and isinstance(
            record.get("flags", {}).get("dnd5e", {}).get("title"),
            str
        )
        and bool(
            record.get("flags", {}).get("dnd5e", {}).get("title").strip()
        )
        for record in data
    )

    if has_entry_title:
        mapping["chapterTitle"] = "flags.dnd5e.title"

    transifex_dict = {
        "label": (
                pack_label
                or JOURNAL_LABEL_OVERRIDES.get(
            pack_name,
            pack_name.title()
        )
        )
    }

    if folders:
        transifex_dict["folders"] = folders

    transifex_dict["mapping"] = mapping
    transifex_dict["entries"] = {}

    for record in data:
        if not isinstance(record, dict):
            continue

        if is_folder_record(record):
            continue

        pages_value = record.get("pages")
        if not isinstance(pages_value, list):
            continue

        entry_name = (record.get("name") or "").strip()
        if not entry_name:
            continue

        entry = {
            "name": entry_name
        }

        chapter_title = (
            record.get("flags", {})
            .get("dnd5e", {})
            .get("title")
        )

        if isinstance(chapter_title, str) and chapter_title.strip():
            entry["chapterTitle"] = chapter_title.strip()

        journal_content = record.get("content")
        if isinstance(journal_content, str) and journal_content.strip():
            entry["description"] = journal_content.strip()

        categories = resolve_journal_categories(
            record.get("categories"),
            id_index
        )

        if categories:
            entry["categories"] = categories

        entry["pages"] = {}

        for page in resolve_journal_pages(pages_value, id_index):
            page_name = (page.get("name") or "").strip()
            if not page_name:
                continue

            page_entry = {
                "name": page_name
            }

            text = extract_journal_page_text(page)
            if text:
                page_entry["text"] = text

            page_entry.update(
                extract_journal_page_descriptions(page)
            )

            page_title = (
                page.get("flags", {})
                .get("dnd5e", {})
                .get("title")
            )

            if isinstance(page_title, str) and page_title.strip():
                page_entry["chapterTitle"] = page_title.strip()

            image = page.get("image")
            if isinstance(image, dict):
                caption = image.get("caption")
                if isinstance(caption, str) and caption.strip():
                    page_entry["caption"] = caption.strip()

            src = page.get("src")
            if isinstance(src, str) and src.strip():
                page_entry["src"] = src.strip()

            video = page.get("video")
            if isinstance(video, dict):
                width = video.get("width")
                height = video.get("height")

                if width not in (None, ""):
                    page_entry["width"] = width

                if height not in (None, ""):
                    page_entry["height"] = height

            entry["pages"][page_name] = page_entry

        if entry["pages"]:
            transifex_dict["entries"][entry_name] = entry

    return remove_empty_keys(transifex_dict)


TOKEN_ARTWORK_DESCRIPTION = '<p><em>Token artwork by <a href="https://www.forgotten-adventures.net/" target="_blank" rel="noopener">Forgotten Adventures</a>.</em></p>'

ACTOR_TYPES = {"character", "npc", "vehicle", "group"}

ACTOR_LABEL_OVERRIDES = {
    "heroes": "Starter Heroes",
    "actors24": "Actors",
    "monsters": "Monsters (SRD)",
}


def actor_mapping_for_pack(pack_name: str) -> dict:
    base = {
        "alignment": {
            "path": "system.details.alignment",
            "converter": "alignment"
        },
        "token": {
            "path": "prototypeToken.sight.range",
            "converter": "sightRange"
        },
        "movement": {
            "path": "system.attributes.movement",
            "converter": "movement"
        },
        "items": {
            "path": "items",
            "converter": "items"
        },
        "senses": {
            "path": "system.attributes.senses",
            "converter": "senses"
        }
    }

    if pack_name == "actors24":
        return {
            "alignment": base["alignment"],
            "token": base["token"],
            "movement": base["movement"],
            "senses": base["senses"],
            "items": base["items"],
            "travel": {
                "path": "system.attributes.travel",
                "converter": "travel"
            },
            "capacityCargo": {
                "path": "system.attributes.capacity.cargo",
                "converter": "weight"
            },
            "effects": {
                "path": "effects",
                "converter": "effects"
            },
            "tokenLight": {
                "path": "prototypeToken.light",
                "converter": "tokenLight"
            },
            "communication": {
                "path": "system.traits.languages.communication",
                "converter": "communication"
            }
        }

    return base


def is_actor_record(record: dict) -> bool:
    if not isinstance(record, dict):
        return False
    return record.get("type") in ACTOR_TYPES and isinstance(record.get("prototypeToken"), dict)


def looks_like_actor_pack(data: list[dict], pack_name: str) -> bool:
    if pack_name in ACTOR_LABEL_OVERRIDES:
        return any(is_actor_record(record) for record in data if isinstance(record, dict))
    return any(is_actor_record(record) for record in data if isinstance(record, dict))


def extract_actor_description(actor: dict, append_token_art: bool = True) -> str:
    details = actor.get("system", {}).get("details", {})
    biography = details.get("biography")
    description = ""

    if isinstance(biography, dict):
        for key in ("value", "public"):
            value = biography.get(key)
            if isinstance(value, str) and value.strip():
                description = value.strip()
                break
    elif isinstance(biography, str) and biography.strip():
        description = biography.strip()

    if not description:
        description = extract_plain_description(actor)

    if append_token_art and TOKEN_ARTWORK_DESCRIPTION not in description:
        description = f"{description}{TOKEN_ARTWORK_DESCRIPTION}" if description else TOKEN_ARTWORK_DESCRIPTION

    return description


def extract_materials(record: dict) -> str:
    materials = record.get("system", {}).get("materials")
    if isinstance(materials, dict):
        value = materials.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(materials, str) and materials.strip():
        return materials.strip()
    return ""


def add_effects_to_entry(entry: dict, effects_value, id_index: dict | None = None) -> None:
    if not effects_value:
        return

    effects = []
    if isinstance(effects_value, list):
        for effect in effects_value:
            if isinstance(effect, str) and id_index:
                resolved = id_index.get(effect)
                if isinstance(resolved, dict):
                    effects.append(resolved)
            elif isinstance(effect, dict):
                effects.append(effect)
    elif isinstance(effects_value, dict):
        effects = list(effects_value.values())

    for effect in effects:
        if not isinstance(effect, dict):
            continue
        name = (effect.get("name") or effect.get("label") or "").strip()
        if not name:
            continue
        entry.setdefault("effects", {})
        effect_entry = {"name": name}
        description = extract_plain_description(effect)
        if not description:
            raw_description = effect.get("description")
            if isinstance(raw_description, str) and raw_description.strip():
                description = raw_description.strip()
        if description:
            effect_entry["description"] = description

        changes = effect.get("changes")
        if isinstance(changes, list):
            translated_changes = {}
            for change in changes:
                if not isinstance(change, dict):
                    continue
                key = change.get("key")
                value = change.get("value")
                if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip():
                    # Tłumaczeniowo istotne są zmiany tekstowe, np. dopisek do nazwy.
                    if key.strip() == "name":
                        translated_changes[key.strip()] = value.strip()
            if translated_changes:
                effect_entry["changes"] = translated_changes

        entry["effects"][name] = effect_entry


def add_activities_to_item(entry: dict, item: dict) -> None:
    activities = item.get("system", {}).get("activities")
    if not isinstance(activities, dict):
        return

    for activity_id, activity in activities.items():
        if not isinstance(activity, dict):
            continue

        name = (activity.get("name") or "").strip()
        activity_key = name or (activity.get("type") if isinstance(activity.get("type"), str) else "") or activity_id
        activity_entry = {}
        if name:
            activity_entry["name"] = name

        condition = activity.get("activation", {}).get("condition")
        if isinstance(condition, str) and condition.strip():
            condition_text = condition.strip()
            activity_entry["condition"] = condition_text
            activity_entry["activationCondition"] = condition_text

        activation_value = activity.get("activation", {}).get("value")
        if isinstance(activation_value, str) and activation_value.strip():
            activity_entry["activationValue"] = activation_value.strip()

        chat_flavor = activity.get("description", {}).get("chatFlavor")
        if isinstance(chat_flavor, str) and chat_flavor.strip():
            activity_entry["chatFlavor"] = chat_flavor.strip()

        target = activity.get("target")
        if isinstance(target, dict):
            affects = target.get("affects")
            if isinstance(affects, dict):
                special = affects.get("special")
                if isinstance(special, str) and special.strip():
                    target_text = special.strip()
                    activity_entry["target"] = target_text
                    activity_entry["targetAffects"] = target_text
            template = target.get("template")
            if "target" not in activity_entry and isinstance(template, dict):
                special = template.get("special")
                if isinstance(special, str) and special.strip():
                    activity_entry["target"] = special.strip()

        range_data = activity.get("range")
        if isinstance(range_data, dict):
            range_special = range_data.get("special")
            if isinstance(range_special, str) and range_special.strip():
                activity_entry["range"] = range_special.strip()

        damage = activity.get("damage")
        if isinstance(damage, dict):
            on_save = damage.get("onSave")
            if isinstance(on_save, str) and on_save.strip():
                activity_entry["damageOnSave"] = on_save.strip()

        roll = activity.get("roll")
        if isinstance(roll, dict):
            roll_name = roll.get("name") or roll.get("prompt")
            if isinstance(roll_name, str) and roll_name.strip():
                activity_entry["roll"] = roll_name.strip()
        elif isinstance(roll, str) and roll.strip():
            activity_entry["roll"] = roll.strip()

        profiles = activity.get("profiles")
        if isinstance(profiles, list):
            profile_entries = {}
            for profile in profiles:
                if not isinstance(profile, dict):
                    continue
                profile_name = profile.get("name")
                if isinstance(profile_name, str) and profile_name.strip():
                    profile_entries[profile_name.strip()] = {"name": profile_name.strip()}
            if profile_entries:
                activity_entry["profiles"] = profile_entries

        if activity_entry:
            entry.setdefault("activities", {})
            entry["activities"][activity_key] = activity_entry


def add_advancement_to_item(entry: dict, item: dict) -> None:
    advancement = item.get("system", {}).get("advancement")

    if not isinstance(advancement, list):
        return

    translated_entries = {}

    for advancement_entry in advancement:
        if not isinstance(advancement_entry, dict):
            continue

        title = advancement_entry.get("title")
        hint = advancement_entry.get("hint")
        advancement_id = advancement_entry.get("_id")

        clean_title = (
            title.strip()
            if isinstance(title, str) and title.strip()
            else ""
        )

        clean_hint = (
            hint.strip()
            if isinstance(hint, str) and hint.strip()
            else ""
        )

        clean_id = (
            advancement_id.strip()
            if isinstance(advancement_id, str) and advancement_id.strip()
            else ""
        )

        # Pomijamy wpisy, które nie zawierają tekstu do tłumaczenia.
        if not clean_title and not clean_hint:
            continue

        # Pierwszy wpis może być kluczowany po title.
        # Kolejne wpisy o identycznym title muszą być kluczowane po _id,
        # aby nie nadpisywały wcześniejszych advancementów.
        key = clean_title or clean_id

        if key in translated_entries:
            key = clean_id

        if not key or key in translated_entries:
            continue

        translated_entry = {}

        if clean_title:
            translated_entry["title"] = clean_title

        if clean_hint:
            translated_entry["hint"] = clean_hint

        translated_entries[key] = translated_entry

    if translated_entries:
        entry["advancement"] = translated_entries


def populate_dnd5e_item(entry: dict, item: dict, id_index: dict | None = None) -> None:
    name = (item.get("name") or "").strip()
    if name:
        entry["name"] = name

    description = extract_plain_description(item)
    if description:
        entry["description"] = description

    requirements = extract_requirements(item)
    if requirements:
        entry["requirements"] = requirements

    materials = extract_materials(item)
    if materials:
        entry["materials"] = materials

    add_nested_string(entry, "chat", item, "system.description.chat")
    add_nested_string(entry, "unidentified", item, "system.description.unidentified")
    add_nested_string(entry, "activation", item, "system.activation.condition")
    add_nested_string(entry, "activationCondition", item, "system.activation.condition")
    add_nested_string(entry, "activationValue", item, "system.activation.value")
    add_nested_string(entry, "targetAffects", item, "system.target.affects.special")

    add_activities_to_item(entry, item)
    add_effects_to_entry(entry, item.get("effects"), id_index)
    add_advancement_to_item(entry, item)


def resolve_actor_item_records(
        actor: dict,
        id_index: dict,
        item_records_by_id: dict[str, list[dict]] | None = None,
        item_record_positions: dict[str, int] | None = None
) -> list[dict]:
    items_value = actor.get("items")
    items = []

    if isinstance(items_value, list):
        for item_ref in items_value:
            if isinstance(item_ref, dict):
                items.append(item_ref)
            elif isinstance(item_ref, str):
                resolved = resolve_record_in_actor_order(
                    item_ref,
                    id_index,
                    item_records_by_id,
                    item_record_positions
                )
                if isinstance(resolved, dict):
                    items.append(resolved)

    return items


def populate_dnd5e_actor(
        entry: dict,
        actor: dict,
        id_index: dict,
        pack_name: str,
        item_records_by_id: dict[str, list[dict]] | None = None,
        item_record_positions: dict[str, int] | None = None
) -> None:
    actor_name = (actor.get("name") or "").strip()
    if actor_name:
        entry["name"] = actor_name

    token_name = actor.get("prototypeToken", {}).get("name")
    if isinstance(token_name, str) and token_name.strip() and token_name.strip() != actor_name:
        entry["tokenName"] = token_name.strip()

    description = extract_actor_description(actor, append_token_art=pack_name in {"heroes", "actors24", "monsters"})
    if description:
        entry["description"] = description

    alignment = extract_alignment(actor)
    if alignment:
        entry["alignment"] = alignment

    items = resolve_actor_item_records(
        actor,
        id_index,
        item_records_by_id,
        item_record_positions
    )
    if items:
        entry.setdefault("items", {})
        for item in items:
            item_name = (item.get("name") or "").strip()
            if not item_name:
                continue
            item_key = item_name
            if item_key in entry["items"] and isinstance(item.get("_id"), str) and item.get("_id"):
                item_key = item["_id"]
            item_entry = {}
            populate_dnd5e_item(item_entry, item, id_index)
            entry["items"][item_key] = item_entry

    add_effects_to_entry(entry, actor.get("effects"), id_index)


def process_actor_pack(data: list[dict], pack_name: str) -> dict:
    id_index = build_id_index(data)
    item_records_by_id = build_records_by_id(data, is_item_record)
    item_record_positions: dict[str, int] = {}

    transifex_dict = {
        "label": ACTOR_LABEL_OVERRIDES.get(pack_name, pack_name.title()),
        "mapping": actor_mapping_for_pack(pack_name),
        "entries": {},
    }

    folders = collect_folder_names(data)

    for record in data:
        if not isinstance(record, dict):
            continue

        name = (record.get("name") or "").strip()
        if not name:
            continue

        if is_folder_record(record):
            continue

        if not is_actor_record(record):
            continue

        entry = {}
        populate_dnd5e_actor(
            entry,
            record,
            id_index,
            pack_name,
            item_records_by_id,
            item_record_positions
        )
        if entry:
            transifex_dict["entries"][name] = entry

    if folders:
        transifex_dict["folders"] = folders

    transifex_dict["entries"] = dict(
        sorted(transifex_dict["entries"].items(), key=lambda item: item[0].casefold())
    )

    return remove_empty_keys(transifex_dict)


ITEM_PACK_LABEL_OVERRIDES = {
    "backgrounds": "Backgrounds (SRD)",
    "classes": "Classes (SRD)",
    "classes24": "Character Classes",
    "classfeatures": "Class & Subclass Features (SRD)",
    "equipment": "Equipment",
    "equipment24": "Equipment",
    "feats": "Feats",
    "feats24": "Feats",
    "items": "Items (SRD)",
    "monsterfeatures": "Monster Features (SRD)",
    "monsterfeatures24": "Monster Features",
    "origins": "Character Origins",
    "origins24": "Character Origins",
    "races": "Races (SRD)",
    "spells": "Spells (SRD)",
    "spells24": "Spells",
    "subclass": "Subclasses (SRD)",
    "subclasses": "Subclasses (SRD)",
    "tradegoods": "Trade Goods",
}

ITEM_PACKS = set(ITEM_PACK_LABEL_OVERRIDES)

ITEM_EXCLUDED_TYPES = {
    "Actor",
    "ActiveEffect",
    "Folder",
    "JournalEntry",
    "JournalEntryPage",
    "RollTable",
    "TableResult",
    "base",
    "folder",
}


def extract_requirements(record: dict) -> str:
    requirements = record.get("system", {}).get("requirements")
    if isinstance(requirements, str) and requirements.strip():
        return requirements.strip()
    return ""


def get_nested_value(record: dict, path: str):
    if not isinstance(record, dict) or not isinstance(path, str) or not path:
        return None

    current = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]

    return current


def get_nested_string(record: dict, path: str) -> str:
    value = get_nested_value(record, path)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def add_nested_string(entry: dict, key: str, record: dict, path: str) -> None:
    value = get_nested_string(record, path)
    if value:
        entry[key] = value


def default_item_mapping() -> dict:
    return {
        "description": "system.description.value",
        "requirements": "system.requirements",
        "materials": "system.materials.value",
        "chat": "system.description.chat",
        "activation": "system.activation.condition",
        "activationValue": "system.activation.value",
        "activities": {
            "path": "system.activities",
            "converter": "structured",
            "cardinality": "many",
            "container": "keyed",
            "keys": ["_id", "name", "type"],
            "mapping": {
                "name": "name",
                "condition": "activation.condition",
                "activationValue": "activation.value",
                "chatFlavor": "description.chatFlavor",
                "duration": "duration.special",
                "roll": "roll.name",
                "damageOnSave": "damage.onSave",
                "range": {
                    "path": "range",
                    "converter": "imperialToMetric"
                },
                "target": {
                    "path": "target",
                    "converter": "imperialToMetric"
                },
                "profiles": {
                    "path": "profiles",
                    "converter": "nameCollection"
                }
            }
        },
        "effects": {
            "path": "effects",
            "converter": "effects"
        },
        "advancement": {
            "path": "system.advancement",
            "converter": "structured",
            "cardinality": "many",
            "container": "keyed",
            "keys": ["_id", "title"],
            "mapping": {
                "title": "title",
                "hint": "hint",
                "_variants": [
                    {
                        "_when": {
                            "path": "type",
                            "equals": "ScaleValue"
                        },
                        "distance": {
                            "path": "configuration.distance",
                            "converter": "imperialToMetric"
                        },
                        "scale": {
                            "path": "configuration.scale",
                            "converter": "imperialToMetric"
                        }
                    }
                ]
            }
        },
        "movement": {
            "path": "system.movement",
            "converter": "imperialToMetric"
        },
        "weight": {
            "path": "system.weight",
            "converter": "imperialToMetric"
        },
        "range": {
            "path": "system.range",
            "converter": "imperialToMetric"
        },
        "capacityWeight": {
            "path": "system.capacity.weight",
            "converter": "imperialToMetric"
        },
        "senses": {
            "path": "system.senses",
            "converter": "imperialToMetric"
        },
        "target": {
            "path": "system.target",
            "converter": "imperialToMetric"
        },
        "volume": {
            "path": "system.capacity.volume",
            "converter": "imperialToMetric"
        }
    }


def spells_legacy_mapping() -> dict:
    return {
        "materials": "system.materials.value",
        "activation": "system.activation.condition",
        "range": {
            "path": "system.range",
            "converter": "range"
        },
        "target": {
            "path": "system.target",
            "converter": "target"
        },
        "effects": {
            "path": "effects",
            "converter": "effects"
        },
        "activities": {
            "path": "system.activities",
            "converter": "activities"
        },
        "rangeActivities": {
            "path": "system.activities",
            "converter": "rangeActivities"
        }
    }


def item_mapping_for_pack(pack_name: str) -> dict:
    return default_item_mapping()


def is_active_effect_record(record: dict) -> bool:
    """
    Rekordy ActiveEffect z eksportu LevelDB mogą mieć type == "base"
    i własne name, np. "Rage". Nie są one samodzielnymi Itemami
    kompendium, tylko efektami osadzanymi w realnym Itemie typu "feat".

    Takie rekordy muszą zostać w id_index, aby można było rozwiązać
    item.effects po _id, ale nie mogą trafić do entries jako osobne wpisy.
    """
    if not isinstance(record, dict):
        return False

    if record.get("type") in {"base", "ActiveEffect"}:
        return True

    return (
            "changes" in record
            and "disabled" in record
            and "duration" in record
            and "transfer" in record
            and not record.get("system")
    )


def is_item_record(record: dict) -> bool:
    if not isinstance(record, dict):
        return False

    if is_active_effect_record(record):
        return False

    record_type = record.get("type")
    if record_type in ITEM_EXCLUDED_TYPES:
        return False

    if is_actor_record(record) or is_folder_record(record):
        return False

    if not (record.get("name") or "").strip():
        return False

    system = record.get("system")
    if not isinstance(system, dict) or not system:
        return False

    return any(
        key in system
        for key in (
            "description",
            "type",
            "activities",
            "requirements",
            "materials",
            "advancement",
            "source",
            "uses",
        )
    )


def collect_folder_names(data: list[dict]) -> dict[str, str]:
    """
    Zbiera foldery na dwa sposoby:

    1. Bezpośrednio z rekordów wyglądających jak foldery.
    2. Przez rozwiązanie ID z pola folder w innych rekordach.

    Przykład:
    item["folder"] == "MLMTCAvKsuFE3vYA"
    oraz rekord folderu ma:
    {
        "_id": "MLMTCAvKsuFE3vYA",
        "name": "Weapon"
    }

    Wynik:
    "folders": {
        "Weapon": "Weapon"
    }
    """
    id_index = build_id_index(data)
    folders: dict[str, str] = {}
    visited: set[str] = set()

    def add_folder_by_id(folder_id: str) -> None:
        if not isinstance(folder_id, str) or not folder_id.strip():
            return

        folder_id = folder_id.strip()

        if folder_id in visited:
            return

        visited.add(folder_id)

        folder_record = id_index.get(folder_id)
        if not isinstance(folder_record, dict):
            return

        folder_name = (folder_record.get("name") or "").strip()
        if folder_name:
            folders[folder_name] = folder_name

        parent_folder_id = folder_record.get("folder")
        if isinstance(parent_folder_id, str) and parent_folder_id.strip():
            add_folder_by_id(parent_folder_id)

    for record in data:
        if not isinstance(record, dict):
            continue

        if is_folder_record(record):
            folder_name = (record.get("name") or "").strip()
            if folder_name:
                folders[folder_name] = folder_name

            parent_folder_id = record.get("folder")
            if isinstance(parent_folder_id, str) and parent_folder_id.strip():
                add_folder_by_id(parent_folder_id)

        folder_id = record.get("folder")
        if isinstance(folder_id, str) and folder_id.strip():
            add_folder_by_id(folder_id)

    return dict(sorted(folders.items(), key=lambda item: item[0].casefold()))


def collect_journal_folder_names(
        data: list[dict]
) -> dict[str, str]:
    """
    Zwraca foldery JournalEntry w kolejności zgodnej
    z eksportem Babele.

    Przy jednakowym sort zachowuje kolejność rekordów
    z paczki źródłowej.
    """
    folder_records = []

    for source_index, record in enumerate(data):
        if not isinstance(record, dict):
            continue

        if not is_folder_record(record):
            continue

        name = (record.get("name") or "").strip()
        if not name:
            continue

        sort_value = record.get("sort")

        if not isinstance(sort_value, (int, float)):
            sort_value = 0

        folder_records.append(
            (sort_value, source_index, name)
        )

    folder_records.sort(
        key=lambda item: item[0]
    )

    return {
        name: name
        for _, _, name in folder_records
    }


def process_item_pack(data: list[dict], pack_name: str) -> dict:
    """
    Standardowy eksport paczek Foundry typu Item.

    Ten procesor jest celowo zawężony do dokumentów Item. Dzięki temu paczki
    items, monsterfeatures i pozostałe item-packi nie wciągają folderów,
    wyników tabel, stron dziennika ani innych rekordów pomocniczych jako entries.
    """
    transifex_dict = {
        "label": ITEM_PACK_LABEL_OVERRIDES.get(pack_name, pack_name.title()),
        "mapping": item_mapping_for_pack(pack_name),
        "entries": {},
    }

    folders = collect_folder_names(data)
    if folders:
        transifex_dict["folders"] = folders

    id_index = build_id_index(data)

    for record in data:
        if not is_item_record(record):
            continue

        name = (record.get("name") or "").strip()
        entry: dict = {}
        populate_dnd5e_item(entry, record, id_index)
        if entry:
            transifex_dict["entries"][name] = entry

    transifex_dict["entries"] = dict(
        sorted(transifex_dict["entries"].items(), key=lambda item: item[0].casefold())
    )

    return remove_empty_keys(transifex_dict)


def process_files(
        folders: str,
        output_dir: str,
        filename_prefix: str | None = None,
        pack_labels: dict[str, str] | None = None
) -> None:
    dict_key = []
    pack_labels = pack_labels or {}
    output_path = pathlib.Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    if filename_prefix is None:
        filename_prefix = output_path.name

    for root, dirs, files in os.walk(folders):
        for file in files:
            if not file.endswith(".json"):
                continue

            file_path = os.path.join(root, file)
            print('Oryginalny plik:', file)

            with open(file_path, 'r', encoding='utf-8') as json_file:
                data = json.load(json_file)

            if not isinstance(data, list):
                print(f"Pomijam {file}: plik nie zawiera listy rekordów.")
                continue

            id_index = build_id_index(data)

            try:
                compendium = data[0]
            except (KeyError, AttributeError, IndexError, TypeError):
                compendium = data

            if not isinstance(compendium, dict):
                print(f"Pomijam {file}: nieprawidłowy format danych.")
                continue

            keys = compendium.keys()
            print('Klucze pliku JSON:', list(keys))

            pack_name = file.split('.')[0]
            new_path = output_path / f'{filename_prefix}.{pack_name}.json'
            new_name = str(new_path)
            print('Nowy plik:', new_name)
            print()

            folders_from_references = collect_folder_names(data)

            if pack_name in TABLE_LABEL_OVERRIDES:
                transifex_dict = process_table_pack(data, pack_name)
                with open(new_name, "w", encoding="utf-8") as outfile:
                    json.dump(transifex_dict, outfile, ensure_ascii=False, indent=4)
                continue

            if pack_name in JOURNAL_LABEL_OVERRIDES or looks_like_journal_pack(data):
                transifex_dict = process_rules_pack(
                    data,
                    pack_name,
                    pack_label=pack_labels.get(pack_name)
                )
                with open(new_name, "w", encoding="utf-8") as outfile:
                    json.dump(transifex_dict, outfile, ensure_ascii=False, indent=4)
                continue

            if pack_name in ITEM_PACKS:
                transifex_dict = process_item_pack(data, pack_name)
                with open(new_name, "w", encoding="utf-8") as outfile:
                    json.dump(transifex_dict, outfile, ensure_ascii=False, indent=4)
                continue

            if looks_like_actor_pack(data, pack_name):
                transifex_dict = process_actor_pack(data, pack_name)
                with open(new_name, "w", encoding="utf-8") as outfile:
                    json.dump(transifex_dict, outfile, ensure_ascii=False, indent=4)
                continue

            folder_json_path = pathlib.Path(root) / f'{pack_name}_folders.json'

            if folder_json_path.is_file():
                transifex_dict = {
                    "label": pack_name.title(),
                    "folders": {},
                    "entries": {},
                    "mapping": {}
                }

                with open(folder_json_path, 'r', encoding='utf-8') as json_file:
                    data_folder = json.load(json_file)

                for new_data in data_folder:
                    name = (new_data.get("name") or "").strip()
                    if name:
                        transifex_dict["folders"][name] = name

            if folders_from_references:
                transifex_dict = {
                    "label": pack_name.title(),
                    "folders": folders_from_references,
                    "entries": {},
                    "mapping": {}
                }
            else:
                transifex_dict = {
                    "label": pack_name.title(),
                    "entries": {},
                    "mapping": {}
                }

            flag = []

            for new_data in data:
                if not isinstance(new_data, dict):
                    continue

                name = (new_data.get("name") or "").strip()
                if not name:
                    continue

                # foldery
                if is_folder_record(new_data):
                    continue

                # Dla pozostałych pakietów tworzymy zwykły entry
                transifex_dict["entries"].setdefault(name, {})
                entry = transifex_dict["entries"][name]
                entry["name"] = name

                # Rekordy przygód / playtestów
                if 'caption' in keys:
                    populate_caption_entry(entry, new_data, id_index, transifex_dict)

                # zwykłe opisy
                if 'prototypeToken' not in keys and pack_name not in ['weapon']:
                    if 'caption' not in keys:
                        flag.append('description')

                    description = extract_description_value(new_data)
                    if not description:
                        description = new_data.get("description", "")

                    if description:
                        entry["description"] = description

                    adjective = new_data.get("system", {}).get("adjective")
                    if isinstance(adjective, str) and adjective.strip():
                        entry["adjective"] = adjective.strip()
                        transifex_dict["mapping"]["adjective"] = "system.adjective"

                    add_actions_from_record(entry, new_data, name, transifex_dict)

                if 'description' in flag and 'caption' not in keys:
                    has_structured_description = any(
                        isinstance(item, dict)
                        and (
                                (
                                        isinstance(item.get("system", {}).get("description"), dict)
                                        and (
                                                "public" in item.get("system", {}).get("description", {})
                                                or "private" in item.get("system", {}).get("description", {})
                                        )
                                )
                                or (
                                        isinstance(item.get("description"), dict)
                                        and (
                                                "public" in item.get("description", {})
                                                or "private" in item.get("description", {})
                                        )
                                )
                        )
                        for item in data
                    )

                    has_system_description = any(
                        isinstance(item, dict)
                        and isinstance(item.get("system"), dict)
                        and "description" in item["system"]
                        for item in data
                    )

                    has_root_description = any(
                        isinstance(item, dict)
                        and "description" in item
                        for item in data
                    )

                    has_dnd5e_description = any(
                        isinstance(item, dict)
                        and isinstance(item.get("system", {}).get("description"), dict)
                        and any(
                            isinstance(item.get("system", {}).get("description", {}).get(key), str)
                            and item.get("system", {}).get("description", {}).get(key).strip()
                            for key in ("value", "chat", "unidentified")
                        )
                        for item in data
                    )

                    if has_dnd5e_description:
                        transifex_dict["mapping"]["description"] = {
                            "path": "system.description",
                            "converter": "structured",
                            "cardinality": "one",
                            "mapping": {
                                "value": "value",
                                "chat": "chat",
                                "unidentified": "unidentified"
                            }
                        }
                    elif has_structured_description:
                        transifex_dict["mapping"]["description"] = {
                            "path": "system.description",
                            "converter": "structured",
                            "cardinality": "one",
                            "mapping": {
                                "public": "public",
                                "private": "private"
                            }
                        }
                    elif has_system_description:
                        transifex_dict["mapping"]["description"] = "system.description"
                    elif has_root_description:
                        transifex_dict["mapping"]["description"] = "description"

                # SPECJALNA OBSŁUGA prototypeToken
                if 'prototypeToken' in keys:
                    populate_prototype_fields(
                        entry=entry,
                        new_data=new_data,
                        id_index=id_index,
                        transifex_dict=transifex_dict
                    )

            transifex_dict = remove_empty_keys(transifex_dict)
            transifex_dict = sort_entries(transifex_dict)

            with open(new_name, "w", encoding='utf-8') as outfile:
                json.dump(transifex_dict, outfile, ensure_ascii=False, indent=4)

            dict_key.append(f'{compendium.keys()}')


def copy_en_json(version_dnd5e: str) -> None:
    source_file = os.path.join("pack_dnd5e", "lang", "en.json")
    destination_dir = version_dnd5e
    destination_file = os.path.join(destination_dir, "en.json")

    os.makedirs(destination_dir, exist_ok=True)
    shutil.copy2(source_file, destination_file)
    print(f"Skopiowano: {source_file} -> {destination_file}")


def move_json_files(version_dnd5e: str) -> None:
    base_path = pathlib.Path(version_dnd5e).resolve()
    target_path = base_path / "compendium"
    target_path.mkdir(parents=True, exist_ok=True)

    json_files = list(base_path.glob("*.json"))

    if not json_files:
        print("Nie znaleziono żadnych plików .json do przeniesienia.")
        return

    for file_path in json_files:
        try:
            shutil.move(str(file_path), str(target_path / file_path.name))
            print(f"Pomyślnie przeniesiono: {file_path.name}")
        except Exception as e:
            print(f"Błąd przy pliku {file_path.name}: {e}")


def clear_directory(path: pathlib.Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

    for item in path.iterdir():
        if item.is_file() or item.is_symlink():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)


def remove_generated_mapping_files(compendium_dir: pathlib.Path, filename_prefix: str) -> None:
    compendium_dir.mkdir(parents=True, exist_ok=True)

    for file_path in compendium_dir.glob(f"{filename_prefix}.*.json"):
        if file_path.is_file():
            file_path.unlink()


def find_local_dnd_folders(base_dir: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        (
            path for path in base_dir.iterdir()
            if path.is_dir()
               and path.name.startswith("dnd")
               and (path / "packs").is_dir()
        ),
        key=lambda path: path.name.casefold()
    )


def copy_local_en_json(source_folder: pathlib.Path, target_folder: pathlib.Path) -> None:
    source_file = source_folder / "lang" / "en.json"
    destination_file = target_folder / "en.json"

    if not source_file.is_file():
        print(f"Brak {source_file}; pomijam kopiowanie en.json.")
        return

    target_folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, destination_file)
    print(f"Skopiowano: {source_file} -> {destination_file}")


def load_pack_labels(
        source_folder: pathlib.Path
) -> dict[str, str]:
    for manifest_name in ("module.json", "system.json"):
        manifest_path = source_folder / manifest_name

        if not manifest_path.is_file():
            continue

        try:
            with open(
                    manifest_path,
                    "r",
                    encoding="utf-8"
            ) as manifest_file:
                manifest = json.load(manifest_file)
        except (OSError, json.JSONDecodeError) as error:
            print(
                f"Nie udało się odczytać "
                f"{manifest_path}: {error}"
            )
            return {}

        labels: dict[str, str] = {}

        for pack in manifest.get("packs", []):
            if not isinstance(pack, dict):
                continue

            pack_name = pack.get("name")

            if (
                    not isinstance(pack_name, str)
                    or not pack_name.strip()
            ):
                pack_path = pack.get("path")

                if isinstance(pack_path, str) and pack_path.strip():
                    pack_name = pathlib.Path(pack_path).name

            label = pack.get("label")

            if (
                    isinstance(pack_name, str)
                    and pack_name.strip()
                    and isinstance(label, str)
                    and label.strip()
            ):
                labels[pack_name.strip()] = label.strip()

        return labels

    return {}


def process_local_dnd_folder(source_folder: pathlib.Path) -> None:
    source_folder = source_folder.resolve()
    filename_prefix = source_folder.name
    packs_dir = source_folder / "packs"
    raw_output_dir = source_folder / "output"
    compendium_dir = source_folder / "compendium"
    pack_labels = load_pack_labels(source_folder)

    print()
    print(f"*** Przetwarzam lokalny folder: {filename_prefix} ***")

    if not packs_dir.is_dir():
        print(f"Pomijam {source_folder}: brak katalogu packs.")
        return

    clear_directory(raw_output_dir)
    remove_generated_mapping_files(compendium_dir, filename_prefix)

    read_leveldb_to_json(str(packs_dir), str(raw_output_dir))
    print()

    process_files(
        folders=str(raw_output_dir),
        output_dir=str(compendium_dir),
        filename_prefix=filename_prefix,
        pack_labels=pack_labels
    )

    copy_local_en_json(source_folder, source_folder)


def process_all_local_dnd_folders(base_dir: pathlib.Path | None = None) -> None:
    if base_dir is None:
        base_dir = pathlib.Path(__file__).resolve().parent
    else:
        base_dir = pathlib.Path(base_dir).resolve()

    local_folders = find_local_dnd_folders(base_dir)

    if not local_folders:
        print(f'Nie znaleziono lokalnych folderów zaczynających się od "dnd" z katalogiem packs w: {base_dir}')
        return

    for source_folder in local_folders:
        process_local_dnd_folder(source_folder)


if __name__ == '__main__':
    process_all_local_dnd_folders()
