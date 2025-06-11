import json
import os
import pathlib
import re
import shutil
import zipfile
from urllib.request import urlretrieve

import plyvel
import requests


def create_version_directory(version):
    if os.path.exists(version):
        print(f'Katalog {version} istnieje, pomijam tworzenie.')
        return False
    else:
        print(f'Tworzę katalog {version}')
        os.makedirs(version)
        return True


def download_and_extract_zip(zip_url, zip_filename, extract_folder_zip):
    response = requests.get(zip_url)
    with open(zip_filename, 'wb') as zip_file:
        zip_file.write(response.content)

    with zipfile.ZipFile(zip_filename, 'r') as zip_file:
        zip_file.extractall(extract_folder_zip)
        print('Pobrano i rozpakowano plik .zip')


def remove_all_braces_from_uuid(data):
    """
    Szuka w wartościach słownika '@UUID[xxx]{xxx}' i usuwa wszystkie wystąpienia '{xxx}' po każdym '@UUID[xxx]'.

    :param data: Słownik do przeszukania i modyfikacji.
    """
    for key, value in data.items():
        if isinstance(value, str) and "@UUID" in value:
            # Usuń wszystkie wystąpienia wzorca {xxx} po @UUID[xxx]
            data[key] = re.sub(r"(@UUID\[[^\]]+\])\{[^}]+\}", r"\1", value)
        elif isinstance(value, dict):
            # Rekurencyjnie przeszukaj zagnieżdżone słowniki
            remove_all_braces_from_uuid(value)


def read_leveldb_to_json(leveldb_path, output_json_path):
    def list_subfolders(directory):
        try:
            # Lista folderów w katalogu
            subfolders = [f.name for f in os.scandir(directory) if f.is_dir()]

            # Zwróć nazwy folderów, jeśli istnieją
            if subfolders:
                return subfolders
            else:
                return "Brak folderów w katalogu"
        except Exception as error:
            raise f"Wystąpił błąd list_subfolders: {error}"

    folders_list = list_subfolders(leveldb_path.replace('\\', '/'))
    for sub_folders in folders_list:
        output_path = rf'{output_json_path}\{sub_folders}.json'
        try:
            output_folder = rf'{output_json_path.split("\\")[0]}\{output_json_path.split("\\")[1]}\packs\{sub_folders}'.replace(
                '\\', '/')
        except IndexError:
            output_folder = rf'{output_json_path.split("\\")[0]}\packs\{sub_folders}'.replace('\\', '/')

        # Ensure the output folder exists
        output_file = output_path.replace('\\', '/')
        output_dir = output_json_path.replace('\\', '/')
        os.makedirs(output_dir, exist_ok=True)

        try:
            # Otwórz bazę danych LevelDB
            db = plyvel.DB(output_folder, create_if_missing=False)

            # Stwórz pustą listę na dane
            data = []

            # Iteruj przez wszystkie klucze i wartości w bazie danych
            for key, value in db:
                try:
                    value_str = value.decode('utf-8', errors='ignore')
                    # Jeśli wartość to poprawny JSON, konwertujemy ją do obiektu
                    try:
                        value_data = json.loads(value_str)
                    except json.JSONDecodeError:
                        value_data = {"name": value_str}  # Jeśli to nie JSON, utwórz obiekt z kluczem "name"

                    # Dodaj tylko wartość do listy
                    data.append(value_data)
                except Exception as e:
                    print(f"Błąd dekodowania dla klucza {key}: {e}")
                    continue

            # Zapisz dane do pliku JSON jako listę
            with open(output_file, 'w', encoding='utf-8') as json_file:
                json.dump(data, json_file, ensure_ascii=False, indent=4)

            print(f"Dane zostały zapisane do {output_file}")
        except Exception as e:
            raise f"Wystąpił błąd read_leveldb_to_json: {e}"
        finally:
            db.close()


def sort_entries(input_dict):
    if "entries" in input_dict:
        input_dict["entries"] = dict(sorted(input_dict["entries"].items()))

    for key, value in input_dict.items():
        if isinstance(value, dict):
            input_dict[key] = sort_entries(value)

    return input_dict


def remove_empty_keys(data_dict):
    """
    Usuwa puste klucze w słowniku i usuwa 'name', jeśli 'pages' jest pusty.
    Proces powtarza się aż do wyeliminowania wszystkich pustych kluczy.

    :param data_dict: Słownik wejściowy
    :return: Oczyszczony słownik
    """

    def clean_dict_once(d):
        """
        Jednokrotne przejście przez słownik w celu usunięcia pustych kluczy.
        """
        cleaned = {}
        for key, value in d.items():
            if isinstance(value, dict):  # Jeśli wartość to słownik, oczyść go rekurencyjnie
                value = clean_dict_once(value)
            if key == "pages" and not value:  # Jeśli "pages" jest pusty
                continue  # Usuń klucz "pages"
            if key == "name" and "pages" in d and not d["pages"]:
                continue  # Usuń klucz "name", jeśli "pages" jest pusty
            if value not in (None, {}, [], ""):  # Usuń inne puste wartości
                cleaned[key] = value
        return cleaned

    previous = None
    current = data_dict

    # Iteruj, aż słownik przestanie się zmieniać
    while previous != current:
        previous = current
        current = clean_dict_once(previous)

    return current


def remove_newlines_from_dict(data):
    """
    Recursively removes all newline characters from values in a nested dictionary.

    :param data: The dictionary to process.
    :return: The updated dictionary with newline characters removed.
    """
    if isinstance(data, dict):
        return {key: remove_newlines_from_dict(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [remove_newlines_from_dict(item) for item in data]
    elif isinstance(data, str):
        return data.replace("\n", "").replace("\t", " ")
    else:
        return data


def process_files(folders, version):
    dict_key = []
    for root, dirs, files in os.walk(folders):
        for file in files:
            if file.endswith(".json"):
                file_path = os.path.join(root, file)
                print('Oryginalny plik:', file)
                with open(file_path, 'r', encoding='utf-8') as json_file:
                    data = json.load(json_file)

                try:
                    compendium = data[0]
                except (KeyError, AttributeError) as e:
                    compendium = data

                keys = compendium.keys()
                print('Klucze pliku JSON:', list(keys))

                new_name = fr'{version}.{file.split('.')[0]}.json'
                # try:
                #     name = compendium['_stats']['systemId'] # Nazwa pobierana z plików, na razie nie używane
                # except KeyError:
                #     print('BŁĄD!!!')
                print('Nowy plik:', new_name)
                print()

                if pathlib.Path(f'{root}/{file.split(".")[0]}_folders.json').is_file():
                    transifex_dict = {
                        "label": file.split('.')[0].title(),
                        "folders": {},
                        "entries": {},
                        "mapping": {}
                    }

                    with open(f'{root}/{file.split(".")[0]}_folders.json', 'r', encoding='utf-8') as json_file:
                        data_folder = json.load(json_file)

                    for new_data in data_folder:
                        name = new_data["name"].strip()
                        transifex_dict["folders"].update({name: name})

                elif 'color' in keys or 'folder' in keys:
                    transifex_dict = {
                        "label": file.split('.')[0].title(),
                        "folders": {},
                        "entries": {},
                        "mapping": {}
                    }
                else:
                    transifex_dict = {
                        "label": file.split('.')[0].title(),
                        "entries": {},
                        "mapping": {}
                    }

                flag = []
                for new_data in data:
                    name = new_data["name"].strip()
                    print(name)

                    # Dla folderów - DZIAŁA
                    if 'folder' in new_data.keys() and 'color' in new_data.keys():
                        transifex_dict["folders"].update({name: name})
                        continue

                    # Dla Kompendium z nazwami
                    elif 'name' in keys:
                        transifex_dict["entries"].update({name: {}})
                        transifex_dict["entries"][name].update({"name": name})

                    # Dla Przygód
                    if 'caption' in keys:
                        transifex_dict['mapping'].update(
                            {
                                "actors":
                                    {
                                        "publicNotes": "system.details.publicNotes",
                                        "privateNotes": "system.details.privateNotes",
                                        "blurb": "system.details.blurb",
                                        "languagesDetails": "system.details.languages.details"
                                    }
                            }
                        )
                        transifex_dict["entries"][name].update({"caption": new_data["caption"]})
                        transifex_dict["entries"][name].update({"description": new_data["description"]})

                        # Foldery
                        if 'folders' in keys:
                            transifex_dict["entries"][name].update({"folders": {}})
                            for folder in new_data["folders"]:
                                if 'folders' in keys:
                                    transifex_dict["entries"][name]["folders"].update({folder['name']: folder['name']})

                        # Dzienniki
                        if 'journal' in keys:
                            transifex_dict["entries"][name.strip()].update({"journals": {}})
                            for journal in new_data["journal"]:
                                transifex_dict["entries"][name]["journals"].update({journal["name"]: {}})
                                transifex_dict["entries"][name]["journals"][journal["name"].strip()].update(
                                    {"name": journal["name"]})
                                transifex_dict["entries"][name]["journals"][journal["name"]].update({"pages": {}})
                                for pages in journal["pages"]:
                                    transifex_dict["entries"][name]["journals"][journal["name"]]["pages"].update(
                                        {pages["name"].strip(): {}})
                                    transifex_dict["entries"][name]["journals"][journal["name"]]["pages"][
                                        pages["name"].strip()].update({"name": pages["name"].strip()})
                                    transifex_dict["entries"][name]["journals"][journal["name"]]["pages"][
                                        pages["name"].strip()].update(
                                        {"text": " ".join(pages["text"].get("content", "").split())})
                        # Sceny
                        if 'scenes' in keys:
                            transifex_dict["entries"][name].update({"scenes": {}})
                            for scene in new_data["scenes"]:
                                transifex_dict["entries"][name]["scenes"].update({scene["name"]: {}})
                                transifex_dict["entries"][name]["scenes"][scene["name"]].update({"name": scene["name"]})
                                transifex_dict["entries"][name]["scenes"][scene["name"]].update({"notes": {}})
                                for note in scene["notes"]:
                                    transifex_dict["entries"][name]["scenes"][scene["name"]]["notes"].update(
                                        {note["text"]: note["text"]})

                        # Makra
                        if 'macros' in keys:
                            transifex_dict["entries"][name].update({"macros": {}})
                            for macro in new_data["macros"]:
                                transifex_dict["entries"][name]["macros"].update({macro["name"]: {}})
                                transifex_dict["entries"][name]["macros"][macro["name"]].update({"name": macro["name"]})

                        # Tabele
                        if 'tables' in keys:
                            transifex_dict["entries"][name].update({"tables": {}})
                            for table in new_data["tables"]:
                                transifex_dict["entries"][name]["tables"].update({table["name"]: {}})
                                transifex_dict["entries"][name]["tables"][table["name"]].update({"name": table["name"]})
                                transifex_dict["entries"][name]["tables"][table["name"]].update({"description": table["description"]})
                                transifex_dict["entries"][name]["tables"][table["name"]].update({"results": {}})
                                for result in table['results']:
                                    result_name = f'{result["range"][0]}-{result["range"][1]}'
                                    transifex_dict["entries"][name]["tables"][table["name"]]['results'].update({result_name: result['text']})

                        # Przedmioty
                        if 'items' in keys:
                            transifex_dict["entries"][name].update({"items": {}})
                            for item in new_data["items"]:
                                transifex_dict["entries"][name]["items"].update({item["name"]: {}})
                                transifex_dict["entries"][name]["items"][item["name"]].update({"name": item["name"]})

                                if item.get('_stats').get('compendiumSource') is None or item.get('_stats').get(
                                        'compendiumSource').startswith('Item'):
                                    transifex_dict["entries"][name]["items"][item["name"]].update({"description": item["system"]["description"]["value"]})
                                    transifex_dict["entries"][name]["items"][item["name"]].update({"gmNote": item["system"]["description"]["gm"]})
                                    transifex_dict['mapping'].update(
                                        {
                                            "gmNote": "system.description.gm"
                                        }
                                    )

                                    # Dla niezidentyfikowanych
                                    try:
                                        if item["system"]["identification"]["unidentified"]["name"] != "":
                                           transifex_dict["entries"][name]["items"][item["name"]].update(
                                               {"unidentified": item["system"]["identification"]["unidentified"]["name"]})
                                           transifex_dict["entries"][name]["items"][item["name"]].update(
                                               {"unidentified_desc": item["system"]["identification"]["unidentified"]["data"]["description"]["value"]})
                                           transifex_dict['mapping'].update(
                                               {
                                                   "unidentified": "system.identification.unidentified.name",
                                                   "unidentified_desc": "system.identification.unidentified.data.description.value",
                                               }
                                           )
                                    except KeyError:
                                        pass

                        # Playlisty
                        if 'playlists' in keys:
                            transifex_dict["entries"][name].update({"playlists": {}})
                            for playlist in new_data["playlists"]:
                                transifex_dict["entries"][name]["playlists"].update({playlist["name"]: {}})
                                transifex_dict["entries"][name]["playlists"][playlist["name"]].update(
                                    {"name": playlist["name"]})
                                transifex_dict["entries"][name]["playlists"][playlist["name"]].update(
                                    {"description": playlist.get("description")})
                                transifex_dict["entries"][name]["playlists"][playlist["name"]].update({"sounds": {}})
                                for sound in playlist["sounds"]:
                                    transifex_dict["entries"][name]["playlists"][playlist["name"]]["sounds"].update(
                                        {sound["name"]: {}})
                                    transifex_dict["entries"][name]["playlists"][playlist["name"]]["sounds"][
                                        sound["name"]].update({"name": sound["name"]})
                                    transifex_dict["entries"][name]["playlists"][playlist["name"]]["sounds"][
                                        sound["name"]].update({"description": sound.get("description")})

                        # Aktorzy
                        if 'actors' in keys:
                            transifex_dict["entries"][name].update({"actors": {}})
                            for actor in new_data["actors"]:
                                transifex_dict["entries"][name]["actors"].update({actor["name"]: {}})
                                transifex_dict["entries"][name]["actors"][actor["name"]].update({"name": actor["name"]})
                                transifex_dict["entries"][name]["actors"][actor["name"]].update(
                                    {"tokenName": actor["prototypeToken"]["name"]})

                                # Tylko PF2E
                                try:
                                    if actor['system']['details']['publicNotes'] != "":
                                        transifex_dict["entries"][name]["actors"][actor["name"]].update(
                                            {"publicNotes": actor['system']['details']['publicNotes']})
                                except KeyError:
                                    pass
                                try:
                                    if actor['system']['details']['blurb'] != "":
                                        transifex_dict["entries"][name]["actors"][actor["name"]].update(
                                            {"blurb": actor['system']['details']['blurb']})
                                except KeyError:
                                    pass
                                try:
                                    if actor['system']['details']['privateNotes'] != "":
                                        transifex_dict["entries"][name]["actors"][actor["name"]].update(
                                            {"privateNotes": actor['system']['details']['privateNotes']})
                                except KeyError:
                                    pass
                                try:
                                    if actor['system']['details']['description'] != "":
                                        transifex_dict["entries"][name]["actors"][actor["name"]].update(
                                            {"description": actor['system']['details']['description']})
                                except KeyError:
                                    pass
                                try:
                                    if actor['system']['details']['languages']['details'] != "":
                                        transifex_dict["entries"][name]["actors"][actor["name"]].update(
                                            {"languagesDetails": actor['system']['details']['languages']['details']})
                                except KeyError:
                                    pass

                                transifex_dict["entries"][name]["actors"][actor["name"]].update({"items": {}})
                                for item in actor['items']:
                                    try:
                                        if item.get("system").get("description").get("gm") != "":
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"].update(
                                                {item["name"]: {}})
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                                item["name"]].update({"name": item["name"]})
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                                item["name"]].update(
                                                {"description": item["system"]["description"]["value"]})

                                            # Dla opisów GM
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                                item["name"]].update({"gmNote": item["system"]["description"]["gm"]})
                                            transifex_dict['mapping']['actors'].update(
                                                {
                                                    "gmNote": "system.description.gm"
                                                }
                                            )
                                    except KeyError:
                                        pass

                                    try:
                                        if item.get("system").get("identification").get("status") == "unidentified":
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"].update(
                                                {item["name"]: {}})
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                                item["name"]].update({"name": item["name"]})
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                                item["name"]].update(
                                                {"description": item["system"]["description"]["value"]})

                                            # Dla niezidentyfikowanych w aktorach
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][item["name"]].update(
                                                {"unidentified":
                                                     item["system"]["identification"]["unidentified"][
                                                         "name"]})
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][item["name"]].update(
                                                {"unidentified_desc":
                                                     item["system"]["identification"]["unidentified"]["data"][
                                                         "description"]["value"]})
                                            transifex_dict['mapping']['actors'].update(
                                                {
                                                    "unidentified": "system.identification.unidentified.name",
                                                    "unidentified_desc": "system.identification.unidentified.data.description.value",
                                                }
                                            )
                                    except AttributeError:
                                        pass

                                    if item["_stats"]["compendiumSource"] is None:
                                        transifex_dict["entries"][name]["actors"][actor["name"]]["items"].update(
                                            {item["name"]: {}})
                                        transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                            item["name"]].update({"name": item["name"]})
                                        transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                            item["name"]].update({"description": item["system"]["description"]["value"]})
                                        if item.get("system").get("description").get("gm") != "":
                                            transifex_dict["entries"][name]["actors"][actor["name"]]["items"][
                                                item["name"]].update({"gmNote": item["system"]["description"]["gm"]})
                                            transifex_dict['mapping']['actors'].update(
                                                {
                                                    "gmNote": "system.description.gm"
                                                }
                                            )

                    # Dla Kompendium z opisami
                    if 'prototypeToken' not in keys and file.split('.')[0] not in ['rules', 'weapon']:
                        if 'caption' not in keys:
                            flag.append('description')
                        try:
                            transifex_dict["entries"][name].update({"description": new_data["system"]["description"]})
                        except KeyError:
                            transifex_dict["entries"][name].update({"description": new_data["description"]})

                    # TODO: Zrobić pregens i rules i summons
                    # Dla Makr
                    elif 'command' in keys:
                        transifex_dict["entries"].update({name: {}})
                        transifex_dict["entries"][name].update({"name": name})

                    # Dla Dzienników
                    elif file.split('.')[0] == 'rules':
                        transifex_dict["entries"].update({name: {}})
                        transifex_dict["entries"][name].update({"name": name})
                        transifex_dict["entries"][name].update({"pages": {}})
                        try:  # Obejscie na umiejętności adventure #TODO: do przetłumaczenia
                            for result in new_data['pages']:
                                for pages in data:
                                    try:
                                        if result == pages['_id']:
                                            transifex_dict["entries"][name]['pages'].update({pages['name']: {}})
                                            transifex_dict["entries"][name]['pages'][pages['name']].update(
                                                {"name": pages['name']})
                                            transifex_dict["entries"][name]['pages'][pages['name']].update(
                                                {"text": pages['text']['content']})
                                    except KeyError:
                                        pass
                        except KeyError:
                            pass

                    # elif 'permission' in keys:
                    #     transifex_dict["entries"].update({name: {}})
                    #     transifex_dict["entries"][name].update({"name": name})
                    #     transifex_dict["entries"][name].update({"pages": {}})
                    #     transifex_dict["entries"][name]['pages'].update({name: {}})
                    #     transifex_dict["entries"][name]['pages'][name].update({"name": name})
                    #     try:
                    #         transifex_dict["entries"][name]['pages'][name].update({"text": new_data['content']})
                    #     except KeyError:
                    #         del transifex_dict["entries"][name]['pages']
                    #         try:
                    #             transifex_dict["entries"][name].update({"description": new_data['data']['description']['value']})
                    #         except KeyError:
                    #             transifex_dict["entries"][name].update(
                    #                 {"description": new_data['system']['description']['value']})
                    #
                    # # Dla tabel
                    # elif 'displayRoll' in keys:
                    #     transifex_dict["entries"].update({name: {}})
                    #     transifex_dict["entries"][name].update({"name": name})
                    #     transifex_dict["entries"][name].update({"description": new_data['description']})
                    #     transifex_dict["entries"][name].update({"results": {}})
                    #     for result in new_data['results']:
                    #         result_name = f'{result["range"][0]}-{result["range"][1]}'
                    #         transifex_dict["entries"][name]['results'].update({result_name: result['text']})

                transifex_dict = remove_empty_keys(transifex_dict)
                transifex_dict = sort_entries(transifex_dict)
                transifex_dict = remove_newlines_from_dict(transifex_dict)
                remove_all_braces_from_uuid(transifex_dict)

                with open(new_name, "w", encoding='utf-8') as outfile:
                    json.dump(transifex_dict, outfile, ensure_ascii=False, indent=4)

                dict_key.append(f'{compendium.keys()}')


def adventures(adventure_url, zip_adventure_filename, zip_adventure, extract_folder):
    path_adventure, headers_adventure = urlretrieve(adventure_url, 'adventure.json')

    try:
        with open('adventure.json', 'r') as file:
            data = json.load(file)
            id_value = data.get('id', None)
    except FileNotFoundError:
        print("Plik nie został znaleziony.")
    except json.JSONDecodeError:
        print("Błąd podczas parsowania pliku JSON.")

    print("*** Wersja przygody: ", id_value, " ***")
    print()

    if create_version_directory(id_value):
        download_and_extract_zip(zip_adventure, zip_adventure_filename, extract_folder)
        print("Pobrano przygodę...")
    else:
        with zipfile.ZipFile(zip_adventure_filename, 'r') as zip_ref:
            zip_ref.extractall(extract_folder)

    # Konwersja z db na json
    read_leveldb_to_json(fr'{extract_folder}\{id_value}\packs',
                         fr'{extract_folder}\{id_value}\output')
    print()


def adventures_local(adventure_url, extract_folder):
    response = requests.get(adventure_url)
    if response.status_code == 200:  # Sprawdza, czy żądanie zakończyło się sukcesem
        with open('adventure.json', 'wb') as file:
            file.write(response.content)  # Zapisuje dane do pliku
        print("Plik został pobrany i zapisany jako adventure.json.")
    else:
        print(f"Błąd: {response.status_code}")

    try:
        with open('adventure.json', 'r') as file:
            data = json.load(file)
            id_value = data.get('id', None)
    except FileNotFoundError:
        print("Plik nie został znaleziony.")
    except json.JSONDecodeError:
        print("Błąd podczas parsowania pliku JSON.")

    print()
    print("*** Wersja przygody lokalnej: ", id_value, " ***")

    # Konwersja z db na json
    read_leveldb_to_json(fr'{id_value}\packs', fr'{id_value}')
    print()


def json_files(adventure_url):
    version_adventure = adventure_url.split('/')[-2]

    folder = rf'pack_adventure/{version_adventure}/output'
    process_files(folder, version_adventure)


if __name__ == '__main__':

    # === === === === === === === === === === === === === === === === === === === === === === === === === === === ===

    # Utworzenie plików do tłumaczenia
    # folder = r'pack_adventure/pf2e-ap196-199-season-of-ghosts/output'
    # process_files(folder, version_adventure)

    # folder2 = r'pack_adventure2/pf2e-ap196-the-summer-that-never-was/output'
    # process_files(folder2, version_adventure2)

    # === === === === === === === === === === === === === === === === === === === === === === === === === === === ===
    adventures_list = [
        {
            "adventure_url": "https://downloads.paizo.com/foundry-public/modules/pf2e-ap196-199-season-of-ghosts/module.json",
            "zip_adventure_filename": "adventure.zip",
            "zip_adventure": "https://downloads.paizo.com/foundry-public/modules/pf2e-ap196-199-season-of-ghosts/module-v12.zip",
            "extract_folder": "pack_adventure",
            "adventure": "season-of-ghosts-pg"
        }
    ]
    adventures_local_list = [
        {
            "adventure_url": "https://cdn.paizo.com/foundry/modules/pf2e-ap196-the-summer-that-never-was/module.json",
            "extract_folder": "pack_adventure",
            "adventure": "season-of-ghosts-1"
        },
        {
            "adventure_url": "https://cdn.paizo.com/foundry/modules/pf2e-ap197-let-the-leaves-fall/module.json",
            "extract_folder": "pack_adventure",
            "adventure": "season-of-ghosts-2"
        },
        {
            "adventure_url": "https://r2.foundryvtt.com/packages-public/pf2e-beginner-box/module.json",
            "extract_folder": "pack_adventure",
            "adventure": "beginner-box"
        },
        {
            "adventure_url": "https://foundryvtt.s3.us-west-2.amazonaws.com/modules/pf2e-kingmaker/module.json",
            "extract_folder": "pack_adventure",
            "adventure": "pf2e-kingmaker"
        },
        {
            "adventure_url": "https://cdn.paizo.com/foundry/modules/pf2e-rusthenge/module.json",
            "extract_folder": "pack_adventure",
            "adventure": "pf2e-rusthenge"
        },
        {
            "adventure_url": "https://r2.foundryvtt.com/packages-public/pf2e-abomination-vaults/module.json",
            "extract_folder": "pack_adventure",
            "adventure": "pf2e-abomination-vaults"
        }
    ]
    for adventure in adventures_list:
        adventures(
            adventure["adventure_url"],
            adventure["zip_adventure_filename"],
            adventure["zip_adventure"],
            adventure["extract_folder"]
        )
        json_files(adventure["adventure_url"])

    for adventure in adventures_local_list:
        adventures_local(
            adventure["adventure_url"],
            adventure["extract_folder"]
        )
        os.makedirs(f'{adventure["adventure_url"].split('/')[-2]}/output', exist_ok=True)
        if os.path.exists('pf2e-abomination-vaults/av.json'):
            os.rename('pf2e-abomination-vaults/av.json', 'pf2e-abomination-vaults/adventures.json')
        shutil.copy(f'{adventure["adventure_url"].split('/')[-2]}/adventures.json',
                    f'{adventure["adventure_url"].split('/')[-2]}/output/adventures.json')
        shutil.copytree(f'{adventure["adventure_url"].split('/')[-2]}',
                        f'{adventure["extract_folder"]}/{adventure["adventure_url"].split('/')[-2]}',
                        dirs_exist_ok=True)
        json_files(adventure["adventure_url"])

    os.rename("pf2e-kingmaker.adventures.json", "pf2e-kingmaker.kingmaker.json")
