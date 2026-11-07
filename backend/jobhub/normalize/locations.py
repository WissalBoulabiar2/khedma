"""Tunisian location normalization: free text -> (governorate, city).

The gazetteer lists the 24 governorates and, for each, its main cities,
delegations and business districts.  Matching is accent/case-insensitive on
word boundaries, longest alias first, so "Hammam Sousse" beats "Sousse" and
"Tunisie" never matches "Tunis".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .text import fold

# governorate slug -> (display name, [aliases of the governorate itself], {city display: [aliases]})
GAZETTEER: dict[str, tuple[str, list[str], dict[str, list[str]]]] = {
    "tunis": ("Tunis", ["tunis", "gouvernorat de tunis", "tunis governorate", "grand tunis", "grand-tunis"], {
        "Tunis": ["tunis", "tunis ville", "tunis centre", "centre ville tunis", "bab bhar", "bab souika", "medina de tunis"],
        "Les Berges du Lac": ["berges du lac", "les berges du lac", "lac 1", "lac 2", "lac 3", "lac i", "lac ii", "le lac", "lac de tunis", "les jardins du lac"],
        "Charguia": ["charguia", "charguia 1", "charguia 2", "la charguia", "cherguia"],
        "Centre Urbain Nord": ["centre urbain nord", "cun", "montplaisir", "mutuelleville", "el omrane", "el menzah", "menzah 1", "menzah 4", "el manar", "manar", "belvedere"],
        "La Marsa": ["la marsa", "marsa", "gammarth", "sidi bou said", "ain zaghouan", "la soukra marsa"],
        "Carthage": ["carthage"],
        "La Goulette": ["la goulette", "goulette", "kram", "le kram"],
        "Le Bardo": ["le bardo", "bardo", "ksar said"],
        "El Ouardia": ["el ouardia", "ouardia", "jebel jelloud", "sidi hassine", "sijoumi", "ezzouhour", "el kabaria", "kabaria"],
    }),
    "ariana": ("Ariana", ["ariana", "l'ariana", "gouvernorat de l'ariana"], {
        "Ariana": ["ariana", "ariana ville", "ennasr", "nasr", "cite ennasr", "menzah 5", "menzah 6", "menzah 7", "menzah 8", "menzah 9", "el menzah 9"],
        "La Soukra": ["la soukra", "soukra", "chotrana", "dar fadhal"],
        "Raoued": ["raoued", "ghazela", "el ghazela", "technopole el ghazala", "ghazala", "pole technologique el ghazala", "jaafar"],
        "Kalâat el-Andalous": ["kalaat el andalous", "kalaat andalous"],
        "Sidi Thabet": ["sidi thabet"],
        "Ettadhamen": ["ettadhamen", "mnihla", "el mnihla"],
    }),
    "ben-arous": ("Ben Arous", ["ben arous", "benarous", "gouvernorat de ben arous"], {
        "Ben Arous": ["ben arous", "benarous", "el yasminette", "yasminette", "nouvelle medina"],
        "Mégrine": ["megrine", "megrine riadh", "sidi rezig", "zi megrine"],
        "Radès": ["rades", "rades port", "port de rades"],
        "Ezzahra": ["ezzahra", "ez zahra"],
        "Hammam Lif": ["hammam lif", "hammam-lif"],
        "Hammam Chott": ["hammam chott", "borj cedria", "bordj cedria", "technopole borj cedria"],
        "Mornag": ["mornag", "khelidia"],
        "Fouchana": ["fouchana"],
        "Mohamedia": ["mohamedia", "mhamdia"],
        "El Mourouj": ["el mourouj", "mourouj"],
        "Boumhel": ["boumhel", "bou mhel"],
        "Ben Arous Zone industrielle": ["zi ben arous", "zone industrielle ben arous"],
    }),
    "manouba": ("Manouba", ["manouba", "la manouba", "mannouba", "gouvernorat de la manouba"], {
        "Manouba": ["manouba", "la manouba", "den den", "denden"],
        "Douar Hicher": ["douar hicher"],
        "Oued Ellil": ["oued ellil", "oued el lil"],
        "Tebourba": ["tebourba"],
        "Mornaguia": ["mornaguia"],
        "Djedeida": ["djedeida", "jedaida", "jdaida"],
        "Borj El Amri": ["borj el amri"],
    }),
    "nabeul": ("Nabeul", ["nabeul", "cap bon", "gouvernorat de nabeul"], {
        "Nabeul": ["nabeul"],
        "Hammamet": ["hammamet", "yasmine hammamet", "hammamet sud", "hammamet nord"],
        "Kélibia": ["kelibia", "klibia"],
        "Korba": ["korba"],
        "Menzel Temime": ["menzel temime"],
        "Grombalia": ["grombalia"],
        "Soliman": ["soliman"],
        "Dar Chaabane": ["dar chaabane", "dar chaabane el fehri"],
        "Béni Khalled": ["beni khalled"],
        "Menzel Bouzelfa": ["menzel bouzelfa"],
        "Bou Argoub": ["bou argoub", "bouargoub"],
        "Béni Khiar": ["beni khiar"],
    }),
    "zaghouan": ("Zaghouan", ["zaghouan", "gouvernorat de zaghouan"], {
        "Zaghouan": ["zaghouan"],
        "Hammam Zriba": ["hammam zriba", "zriba"],
        "El Fahs": ["el fahs", "fahs"],
        "Bir Mcherga": ["bir mcherga"],
        "Nadhour": ["nadhour"],
    }),
    "bizerte": ("Bizerte", ["bizerte", "binzart", "gouvernorat de bizerte"], {
        "Bizerte": ["bizerte"],
        "Menzel Bourguiba": ["menzel bourguiba"],
        "Mateur": ["mateur"],
        "Ras Jebel": ["ras jebel", "ras djebel"],
        "Menzel Jemil": ["menzel jemil"],
        "Menzel Abderrahmane": ["menzel abderrahmane"],
        "Utique": ["utique"],
        "El Alia": ["el alia"],
    }),
    "beja": ("Béja", ["beja", "gouvernorat de beja"], {
        "Béja": ["beja"],
        "Medjez el-Bab": ["medjez el bab", "mejez el bab"],
        "Testour": ["testour"],
        "Téboursouk": ["teboursouk"],
        "Nefza": ["nefza"],
    }),
    "jendouba": ("Jendouba", ["jendouba", "gouvernorat de jendouba"], {
        "Jendouba": ["jendouba"],
        "Tabarka": ["tabarka"],
        "Aïn Draham": ["ain draham"],
        "Bou Salem": ["bou salem", "bousalem"],
        "Ghardimaou": ["ghardimaou"],
    }),
    "le-kef": ("Le Kef", ["le kef", "el kef", "kef", "gouvernorat du kef"], {
        "Le Kef": ["le kef", "el kef", "kef"],
        "Dahmani": ["dahmani"],
        "Tajerouine": ["tajerouine"],
    }),
    "siliana": ("Siliana", ["siliana", "gouvernorat de siliana"], {
        "Siliana": ["siliana"],
        "Makthar": ["makthar", "maktar"],
        "Gaâfour": ["gaafour"],
        "Bou Arada": ["bou arada"],
    }),
    "sousse": ("Sousse", ["sousse", "gouvernorat de sousse", "sousse governorate"], {
        "Sousse": ["sousse", "sousse ville", "sahloul", "khezama", "sousse riadh", "sousse jaouhara", "kantaoui", "port el kantaoui", "technopole de sousse", "novation city"],
        "Hammam Sousse": ["hammam sousse"],
        "Msaken": ["msaken", "m'saken"],
        "Akouda": ["akouda"],
        "Kalâa Kebira": ["kalaa kebira", "kalaa kbira"],
        "Kalâa Seghira": ["kalaa seghira", "kalaa sghira"],
        "Enfidha": ["enfidha", "enfida"],
        "Bouficha": ["bouficha"],
        "Sidi Bou Ali": ["sidi bou ali"],
        "Zaouiet Sousse": ["zaouiet sousse"],
        "Hergla": ["hergla"],
    }),
    "monastir": ("Monastir", ["monastir", "gouvernorat de monastir"], {
        "Monastir": ["monastir", "skanes"],
        "Ksar Hellal": ["ksar hellal"],
        "Moknine": ["moknine"],
        "Jemmal": ["jemmal", "jammel"],
        "Sahline": ["sahline"],
        "Téboulba": ["teboulba"],
        "Ksibet el-Médiouni": ["ksibet el mediouni", "ksibet mediouni"],
        "Bembla": ["bembla"],
        "Zéramdine": ["zeramdine"],
        "Bekalta": ["bekalta"],
        "Ouerdanine": ["ouerdanine"],
        "Lamta": ["lamta"],
        "Sayada": ["sayada"],
    }),
    "mahdia": ("Mahdia", ["mahdia", "gouvernorat de mahdia"], {
        "Mahdia": ["mahdia"],
        "El Jem": ["el jem", "eljem", "el djem"],
        "Ksour Essef": ["ksour essef", "ksour essaf"],
        "Chebba": ["chebba"],
        "Bou Merdes": ["bou merdes"],
        "Souassi": ["souassi"],
    }),
    "sfax": ("Sfax", ["sfax", "gouvernorat de sfax"], {
        "Sfax": ["sfax", "sfax ville", "sfax sud", "sfax nord", "technopole de sfax", "poudriere"],
        "Sakiet Ezzit": ["sakiet ezzit", "sakiet ez zit"],
        "Sakiet Eddaïer": ["sakiet eddaier", "sakiet ed daier"],
        "Thyna": ["thyna"],
        "El Aïn": ["el ain sfax", "route el ain"],
        "Mahrès": ["mahres"],
        "Agareb": ["agareb"],
        "Jebiniana": ["jebiniana"],
        "Kerkennah": ["kerkennah"],
        "Gremda": ["gremda", "route gremda"],
        "Chihia": ["chihia"],
    }),
    "kairouan": ("Kairouan", ["kairouan", "gouvernorat de kairouan"], {
        "Kairouan": ["kairouan"],
        "Haffouz": ["haffouz"],
        "Sbikha": ["sbikha"],
        "Bouhajla": ["bouhajla"],
        "Oueslatia": ["oueslatia"],
    }),
    "kasserine": ("Kasserine", ["kasserine", "gouvernorat de kasserine"], {
        "Kasserine": ["kasserine"],
        "Sbeitla": ["sbeitla"],
        "Fériana": ["feriana"],
        "Thala": ["thala"],
    }),
    "sidi-bouzid": ("Sidi Bouzid", ["sidi bouzid", "gouvernorat de sidi bouzid"], {
        "Sidi Bouzid": ["sidi bouzid"],
        "Regueb": ["regueb"],
        "Meknassy": ["meknassy"],
        "Menzel Bouzaiane": ["menzel bouzaiane"],
    }),
    "gabes": ("Gabès", ["gabes", "gouvernorat de gabes"], {
        "Gabès": ["gabes"],
        "Ghannouch": ["ghannouch"],
        "Mareth": ["mareth"],
        "El Hamma": ["el hamma"],
        "Métouia": ["metouia"],
    }),
    "medenine": ("Médenine", ["medenine", "gouvernorat de medenine"], {
        "Médenine": ["medenine"],
        "Djerba": ["djerba", "jerba", "houmt souk", "houmt essouk", "midoun", "djerba midoun", "djerba houmt souk", "ajim"],
        "Zarzis": ["zarzis"],
        "Ben Gardane": ["ben gardane", "ben guerdane"],
    }),
    "tataouine": ("Tataouine", ["tataouine", "gouvernorat de tataouine"], {
        "Tataouine": ["tataouine"],
        "Ghomrassen": ["ghomrassen"],
        "Remada": ["remada"],
        "Chenini": ["chenini"],
    }),
    "gafsa": ("Gafsa", ["gafsa", "gouvernorat de gafsa"], {
        "Gafsa": ["gafsa"],
        "Métlaoui": ["metlaoui"],
        "Redeyef": ["redeyef"],
        "Moularès": ["moulares", "om larayes"],
        "El Ksar": ["el ksar gafsa"],
    }),
    "tozeur": ("Tozeur", ["tozeur", "gouvernorat de tozeur"], {
        "Tozeur": ["tozeur"],
        "Nefta": ["nefta"],
        "Degache": ["degache"],
    }),
    "kebili": ("Kébili", ["kebili", "gouvernorat de kebili"], {
        "Kébili": ["kebili"],
        "Douz": ["douz"],
        "Souk Lahad": ["souk lahad"],
    }),
}

GOVERNORATES: dict[str, str] = {slug: data[0] for slug, data in GAZETTEER.items()}

_TN_COUNTRY = {"tn", "tun", "tunisia", "tunisie", "tunesien", "tunez", "تونس", "republic of tunisia", "republique tunisienne"}
_TN_TEXT = re.compile(r"\b(tunisia|tunisie|tunisian|tunisienne?)\b")


@dataclass(frozen=True)
class Location:
    governorate: str | None  # slug
    city: str | None         # display name

    @property
    def governorate_name(self) -> str | None:
        return GOVERNORATES.get(self.governorate) if self.governorate else None


@lru_cache(maxsize=1)
def _patterns() -> tuple[list[tuple[re.Pattern[str], str, str | None]], ...]:
    city_aliases: list[tuple[str, str, str | None]] = []
    gov_aliases: list[tuple[str, str, str | None]] = []
    for slug, (_name, gov_al, cities) in GAZETTEER.items():
        for alias in gov_al:
            gov_aliases.append((fold(alias), slug, None))
        for city, aliases in cities.items():
            for alias in aliases:
                city_aliases.append((fold(alias), slug, city))

    def compile_(items: list[tuple[str, str, str | None]]):
        items = sorted(set(items), key=lambda x: -len(x[0]))
        return [(re.compile(r"(?<![a-z0-9])" + re.escape(a).replace(r"\ ", r"[\s\-_]+") + r"(?![a-z0-9])"), s, c)
                for a, s, c in items]

    return compile_(city_aliases), compile_(gov_aliases)


@lru_cache(maxsize=1)
def _governorate_aliases() -> frozenset[str]:
    return frozenset(fold(a) for _n, aliases, _c in GAZETTEER.values() for a in aliases)


def resolve_location(*texts: str | None) -> Location:
    """Find governorate/city in the given strings (most specific string first)."""
    city_pats, gov_pats = _patterns()
    gov_names = _governorate_aliases()
    folded = [fold(t) for t in texts if t]
    # 1) A city/district alias wins: it implies its governorate.  Within one
    #    string prefer a specific place over a name that is also a governorate
    #    ("Chenini, Tataouine" -> Chenini), then the earliest, then the longest.
    for t in folded:
        best: tuple[tuple[int, int, int], str, str | None] | None = None
        for pat, slug, city in city_pats:
            m = pat.search(t)
            if m:
                rank = (1 if m.group(0) in gov_names else 0, m.start(), -(m.end() - m.start()))
                if best is None or rank < best[0]:
                    best = (rank, slug, city)
        if best is not None:
            return Location(best[1], best[2])
    # 2) Otherwise a governorate name.
    for t in folded:
        for pat, slug, _ in gov_pats:
            if pat.search(t):
                return Location(slug, None)
    return Location(None, None)


def is_tunisia(country: str | None, *texts: str | None) -> bool | None:
    """True/False when we can tell; None when nothing indicates a country."""
    if country:
        return fold(country) in _TN_COUNTRY
    for t in texts:
        f = fold(t)
        if not f:
            continue
        if _TN_TEXT.search(f):
            return True
    if resolve_location(*texts).governorate:
        return True
    return None


_FOREIGN = re.compile(
    r"\b(france|allemagne|germany|deutschland|italie|italy|espagne|spain|portugal|belgique|belgium|suisse|switzerland|"
    r"luxembourg|pays[- ]bas|netherlands|royaume[- ]uni|united kingdom|uk|irlande|ireland|canada|usa|etats[- ]unis|"
    r"united states|algerie|algeria|alger|algiers|maroc|morocco|casablanca|libye|libya|tripoli|egypte|egypt|cairo|le caire|"
    r"mauritanie|mauritania|senegal|dakar|cote d'ivoire|ivory coast|cameroun|cameroon|qatar|doha|arabie saoudite|"
    r"saudi arabia|ksa|riyadh|emirats|uae|united arab emirates|dubai|abu dhabi|koweit|kuwait|oman|bahrein|bahrain|"
    r"jordanie|jordan|liban|lebanon|turquie|turkey|malte|malta|roumanie|romania|pologne|poland|south africa|nigeria|kenya)\b")


def foreign_country_in(text: str | None) -> bool:
    return bool(text) and bool(_FOREIGN.search(fold(text)))


def governorate_choices() -> list[tuple[str, str]]:
    return sorted(GOVERNORATES.items(), key=lambda kv: fold(kv[1]))
