"""Controlled vocabularies and classifiers.

Sources use wildly different labels ("Full-time", "CDI / SIVP", "Entre 2 et 5
ans", "Métiers : Banque / Assurances").  Everything is mapped onto the small
vocabularies below so filters behave consistently across sources.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .text import fold

# ------------------------------------------------------------------ domains --
# domain slug -> (label, {specialization slug: (label, [keywords])})
# Keywords are matched accent-insensitively on word boundaries.
DOMAINS: dict[str, tuple[str, dict[str, tuple[str, list[str]]]]] = {
    "informatique": ("Informatique & IT", {
        "developpement-logiciel": ("Développement logiciel", [
            "developpeur", "developpeuse", "developer", "software engineer", "ingenieur logiciel", "programmeur",
            "full stack", "fullstack", "backend", "back-end", "back end", "java", "python", ".net", "dotnet", "c#", "c++",
            "php", "symfony", "laravel", "node.js", "nodejs", "golang", "spring boot", "software developer", "ingenieur developpement",
            "ingenieur etudes et developpement", "tech lead", "architecte logiciel", "odoo", "embedded software", "logiciel embarque"]),
        "web-mobile": ("Web & Mobile", [
            "frontend", "front-end", "front end", "react", "angular", "vue.js", "vuejs", "flutter", "android", "ios",
            "mobile developer", "developpeur mobile", "integrateur web", "web developer", "developpeur web", "wordpress", "react native", "next.js"]),
        "data-ia": ("Data & Intelligence artificielle", [
            "data scientist", "data engineer", "data analyst", "machine learning", "deep learning", "intelligence artificielle",
            "artificial intelligence", "ia", "ai engineer", "llm", "nlp", "computer vision", "big data", "business intelligence",
            "power bi", "data science", "mlops", "bi analyst", "analyste bi", "data modeler", "data quality", "generative ai", "genai"]),
        "devops-cloud": ("DevOps & Cloud", [
            "devops", "cloud", "aws", "azure", "gcp", "kubernetes", "docker", "sre", "site reliability", "terraform", "ci/cd",
            "ingenieur systeme", "administrateur systeme", "system administrator", "sysadmin", "linux", "observabilite", "dynatrace"]),
        "cybersecurite": ("Cybersécurité", [
            "cybersecurite", "cybersecurity", "securite informatique", "security engineer", "soc", "pentest", "pentester",
            "rssi", "ciso", "security officer", "iam", "securite des systemes d'information", "grc"]),
        "reseaux-telecom": ("Réseaux & Télécoms", [
            "reseau", "reseaux", "network", "telecom", "telecoms", "telecommunication", "cisco", "ccna", "fibre optique",
            "radio", "5g", "4g", "voip", "infrastructure"]),
        "support-it": ("Support & Infrastructure IT", [
            "support informatique", "helpdesk", "help desk", "technicien informatique", "it support", "support technique",
            "administrateur reseau", "service desk", "technical support", "informaticien", "technicien helpdesk", "it helpdesk"]),
        "qa-test": ("Qualité logicielle & Test", [
            "qa", "testeur", "tester", "test engineer", "ingenieur test", "ingenieur qa", "automatisation des tests",
            "quality assurance", "selenium", "test automation", "validation logicielle"]),
        "erp-gestion-projet-it": ("ERP, Produit & Projets IT", [
            "erp", "sap", "consultant fonctionnel", "chef de projet it", "chef de projet informatique", "product owner",
            "product manager", "scrum master", "business analyst", "moa", "amoa", "it project manager", "salesforce", "dynamics 365", "crm"]),
    }),
    "ingenierie-industrie": ("Ingénierie & Industrie", {
        "mecanique": ("Mécanique", ["mecanique", "mecanicien", "mechanical", "conception mecanique", "catia", "solidworks", "dessinateur", "projeteur"]),
        "electrique-electronique": ("Électrique & Électronique", [
            "electrique", "electricien", "electricite", "climatisation", "frigoriste", "biomedical", "biomedicale", "electrotechnique", "electronique", "electrical", "electronics", "automatisme",
            "automaticien", "plc", "automates", "hardware", "pcb", "systemes embarques", "embedded"]),
        "production": ("Production & Méthodes", [
            "production", "chef de production", "responsable production", "ingenieur production", "methodes", "process",
            "industrialisation", "lean", "operateur", "operatrice", "chef d'equipe", "superviseur production", "usine", "manufacturing"]),
        "maintenance": ("Maintenance", ["maintenance", "technicien de maintenance", "maintenance industrielle", "gmao"]),
        "qualite": ("Qualité, Hygiène, Sécurité & Environnement", [
            "qualite", "quality", "qhse", "hse", "hsse", "controle qualite", "assurance qualite", "iso 9001", "auditeur qualite",
            "securite au travail", "environnement", "quality engineer", "ingenieur qualite", "responsable qualite", "conformite"]),
        "automobile-cablage": ("Automobile & Câblage", ["cablage", "faisceaux", "wiring harness", "automobile", "automotive", "vehicule"]),
        "aeronautique": ("Aéronautique", ["aeronautique", "aerospace", "aviation", "avionique"]),
        "energie": ("Énergie, Fluides & Pétrole", ["energie", "energy", "petrole", "oil", "gaz", "photovoltaique", "solaire", "renouvelable",
                                                   "fluides", "hydraulique", "thermique", "cvc"]),
        "textile": ("Textile & Habillement", ["textile", "confection", "habillement", "couture", "modeliste", "styliste"]),
    }),
    "btp-architecture": ("BTP, Génie civil & Architecture", {
        "genie-civil": ("Génie civil & BTP", ["genie civil", "btp", "chantier", "conducteur de travaux", "ingenieur structure", "batiment", "travaux publics", "metreur", "topographe", "geometre"]),
        "architecture": ("Architecture & Design d'intérieur", ["architecte", "architecture", "architecte d'interieur", "decorateur", "urbaniste"]),
    }),
    "finance-comptabilite": ("Finance, Banque & Comptabilité", {
        "comptabilite": ("Comptabilité", ["comptable", "comptabilite", "accountant", "accounting", "expert comptable", "aide comptable", "fiscalite", "fiscaliste", "tresorerie", "tresorier", "accounts payable", "accounts receivable", "paie comptable", "facturation", "billing"]),
        "audit-controle": ("Audit & Contrôle de gestion", ["audit", "auditeur", "auditor", "controle de gestion", "controleur de gestion", "controller", "controle interne", "commissaire aux comptes", "compliance", "risk", "risques"]),
        "finance": ("Finance d'entreprise", ["finance", "financier", "daf", "raf", "responsable administratif et financier", "financial analyst", "analyste financier", "fp&a", "credit", "recouvrement", "collections"]),
        "banque-assurance": ("Banque & Assurance", ["banque", "bancaire", "bank", "banking", "assurance", "insurance", "microfinance", "charge de clientele", "conseiller clientele", "chargee de clientele", "gestionnaire de portefeuille", "agence bancaire", "actuaire"]),
    }),
    "commercial-vente": ("Commercial & Vente", {
        "commercial": ("Commercial & Business development", ["commercial", "commerciale", "business developer", "business development", "sales", "ingenieur commercial", "account manager", "key account", "kam", "attache commercial", "prospecteur", "delegue commercial", "technico-commercial", "technico commercial", "sdr", "bdr", "sales development"]),
        "immobilier": ("Immobilier", ["immobilier", "immobiliere", "agent immobilier", "promotion immobiliere", "syndic"]),
        "vente-retail": ("Vente & Distribution", ["vendeur", "vendeuse", "vente", "conseiller de vente", "conseillere de vente", "conseillere de ventes", "employee commerciale", "employe commercial", "caissier", "caissiere", "chef de rayon", "magasin", "boutique", "retail", "store manager", "responsable magasin", "merchandiser", "branch manager"]),
        "delegue-medical": ("Délégué médical", ["delegue medical", "deleguee medicale", "visiteur medical", "medical representative"]),
        "import-export": ("Import / Export & Commerce international", ["import", "export", "commerce international", "transitaire", "douane", "transit"]),
    }),
    "marketing-communication": ("Marketing & Communication", {
        "marketing": ("Marketing", ["marketing", "chef de produit", "brand manager", "growth", "trade marketing", "category manager"]),
        "digital": ("Marketing digital & Social media", ["marketing digital", "digital marketing", "community manager", "social media", "seo", "sea", "content manager", "redacteur web", "copywriter", "traffic manager", "e-commerce", "ecommerce"]),
        "communication": ("Communication & Évènementiel", ["communication", "chargee de communication", "charge de communication", "relations publiques", "evenementiel", "event", "journaliste", "media"]),
        "design": ("Design graphique & UX", ["designer", "graphiste", "infographiste", "ui/ux", "ux", "ui designer", "motion design", "video", "monteur video", "photographe", "directeur artistique"]),
    }),
    "relation-client": ("Relation client & Centres d'appels", {
        "centre-appels": ("Téléconseil & Centre d'appels", ["teleconseiller", "teleconseillere", "teleoperateur", "teleoperatrice", "call center", "centre d'appels", "centre d'appel", "televente", "televendeur", "teleprospection", "telemarketing", "agent de saisie", "reception d'appel", "reception d'appels", "emission d'appels", "conseiller clientele", "chargee de clientele", "charge de clientele"]),
        "service-client": ("Service & Support client", ["service client", "customer service", "customer support", "customer experience", "relation client", "chargee de relation client", "support client", "customer success", "conseiller client", "hotline"]),
    }),
    "rh-juridique": ("RH, Juridique & Administration", {
        "rh": ("Ressources humaines", ["ressources humaines", "rh", "human resources", "hr", "recrutement", "recruteur", "recruiter", "talent acquisition", "paie", "payroll", "people business partner", "gestionnaire de paie", "formation du personnel", "drh", "rrh"]),
        "juridique": ("Juridique", ["juriste", "juridique", "legal", "avocat", "lawyer", "counsel", "contrats", "notaire", "affaires juridiques"]),
        "administration": ("Assistanat & Administration", ["assistante", "assistant de direction", "assistante de direction", "secretaire", "office manager", "administratif", "administrative", "receptionniste", "standardiste", "agent administratif", "gestionnaire administratif"]),
    }),
    "logistique-achats": ("Logistique, Transport & Achats", {
        "logistique": ("Logistique & Supply chain", ["logistique", "logistics", "supply chain", "magasinier", "gestionnaire de stock", "entrepot", "warehouse", "planificateur", "planning", "approvisionnement", "ordonnancement", "inventaire", "agent de tri", "depot"]),
        "transport": ("Transport & Chauffeurs", ["chauffeur", "livreur", "driver", "transport", "coursier", "conducteur poids lourds", "flotte", "chef de parc", "parc automobile"]),
        "achats": ("Achats", ["achat", "achats", "acheteur", "acheteuse", "purchasing", "procurement", "sourcing", "purchaser"]),
    }),
    "sante-pharma": ("Santé, Pharma & Sciences", {
        "medical": ("Médical & Paramédical", ["medecin", "infirmier", "infirmiere", "sage-femme", "kinesitherapeute", "kine", "general practitioner", "telemedicine", "behavioral health", "pharmacien", "dentiste", "medical", "clinique", "hopital", "paramedical", "psychologue", "psychologist", "counsellor", "aide soignant", "nurse"]),
        "pharma": ("Industrie pharmaceutique", ["pharmaceutique", "pharma", "affaires reglementaires", "regulatory affairs", "pharmacovigilance", "biotech", "biologie", "biologiste"]),
        "laboratoire": ("Laboratoire & Chimie", ["laboratoire", "laborantin", "technicien de laboratoire", "chimiste", "chimie", "analyses", "microbiologie"]),
    }),
    "hotellerie-tourisme": ("Hôtellerie, Tourisme & Restauration", {
        "hotellerie": ("Hôtellerie", ["hotel", "hotellerie", "receptionniste hotel", "gouvernante", "front office", "night audit", "housekeeping", "animateur", "animation"]),
        "restauration": ("Restauration", ["restaurant", "restauration", "cuisinier", "chef de cuisine", "commis", "patissier", "serveur", "serveuse", "barman", "chef de partie", "boulanger", "barista", "crepier", "pizzaiolo", "plongeur", "cafe"]),
        "tourisme": ("Tourisme & Voyage", ["tourisme", "agence de voyage", "voyage", "billetterie", "travel", "guide touristique"]),
    }),
    "education-formation": ("Éducation & Formation", {
        "enseignement": ("Enseignement", ["enseignant", "enseignante", "professeur", "teacher", "instituteur", "institutrice", "educatrice", "educateur", "tuteur", "tutor", "universite", "ecole"]),
        "formation": ("Formation professionnelle", ["formateur", "formatrice", "trainer", "formation", "coach", "learning"]),
    }),
    "agriculture-agroalimentaire": ("Agriculture & Agroalimentaire", {
        "agroalimentaire": ("Agroalimentaire", ["agroalimentaire", "agro-alimentaire", "food", "industrie alimentaire", "biscuiterie", "conserverie", "huilerie", "laiterie"]),
        "agriculture": ("Agriculture", ["agricole", "agriculture", "agronome", "ingenieur agronome", "elevage", "veterinaire", "irrigation", "pepiniere"]),
    }),
    "direction-management": ("Direction & Management", {
        "direction": ("Direction générale & Management", ["directeur general", "directrice generale", "general manager", "ceo", "coo", "country manager", "directeur", "directrice", "head of", "managing director", "chief"]),
        "gestion-projet": ("Gestion de projet & Conseil", ["chef de projet", "project manager", "gestion de projet", "pmo", "consultant", "consultante", "conseil", "chargee de mission", "charge de mission", "coordinateur", "coordinatrice", "project coordinator"]),
    }),
    "autres": ("Autres métiers", {
        "services-generaux": ("Services généraux, Sécurité & Artisanat", ["agent de securite", "gardien", "agent d'entretien", "femme de menage", "nettoyage", "jardinier", "plombier", "menuisier", "soudeur", "technicien polyvalent", "peintre", "macon", "carreleur", "tolier"]),
        "beaute": ("Beauté & Bien-être", ["estheticienne", "estheticien", "coiffeur", "coiffeuse", "spa", "masseur", "masseuse", "onglerie", "maquilleuse"]),
        "autre": ("Autre", []),
    }),
}

DOMAIN_LABELS = {slug: label for slug, (label, _) in DOMAINS.items()}
SPECIALIZATION_LABELS = {spec: label for _, (_, specs) in DOMAINS.items() for spec, (label, _) in specs.items()}
SPECIALIZATION_DOMAIN = {spec: dom for dom, (_, specs) in DOMAINS.items() for spec in specs}

# Source category labels (e.g. Keejob "Métiers") that map straight to a specialization.
_CATEGORY_HINTS: dict[str, str] = {
    "informatique": "developpement-logiciel", "it": "developpement-logiciel", "developpement": "developpement-logiciel",
    "banque": "banque-assurance", "assurance": "banque-assurance", "finance": "finance", "comptabilite": "comptabilite",
    "audit": "audit-controle", "commercial": "commercial", "vente": "vente-retail", "marketing": "marketing",
    "communication": "communication", "ressources humaines": "rh", "juridique": "juridique", "logistique": "logistique",
    "transport": "transport", "achats": "achats", "sante": "medical", "medical": "medical", "pharmacie": "pharma",
    "hotellerie": "hotellerie", "tourisme": "tourisme", "restauration": "restauration", "enseignement": "enseignement",
    "formation": "formation", "agriculture": "agriculture", "agroalimentaire": "agroalimentaire", "btp": "genie-civil",
    "genie civil": "genie-civil", "architecture": "architecture", "industrie": "production", "production": "production",
    "maintenance": "maintenance", "qualite": "qualite", "electrique": "electrique-electronique", "electronique": "electrique-electronique",
    "mecanique": "mecanique", "telecom": "reseaux-telecom", "telecommunications": "reseaux-telecom", "centre d'appels": "centre-appels",
    "call center": "centre-appels", "relation client": "service-client", "administration": "administration", "secretariat": "administration",
    "assistanat": "administration", "textile": "textile", "design": "design", "graphisme": "design", "direction": "direction",
    "management": "direction", "conseil": "gestion-projet", "immobilier": "immobilier", "esthetique": "beaute",
    "beaute": "beaute", "coiffure": "beaute", "nettoyage": "services-generaux", "energie": "energie", "aeronautique": "aeronautique", "automobile": "automobile-cablage",
}


def _kw_regex(words: list[str]) -> re.Pattern[str] | None:
    words = [fold(w) for w in words if w]
    if not words:
        return None
    alts = "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
    # Optional plural / inclusive endings: "téléconseillers", "chargé(e)s", "vendeurs.ses".
    return re.compile(r"(?<![a-z0-9])(?:" + alts + r")(?:s|x|\(e\)s?|\(s\)|e?s?)?(?![a-z0-9])")


@lru_cache(maxsize=1)
def _spec_patterns() -> dict[str, re.Pattern[str]]:
    out = {}
    for _dom, (_label, specs) in DOMAINS.items():
        for spec, (_l, kws) in specs.items():
            pat = _kw_regex(kws)
            if pat is not None:
                out[spec] = pat
    return out


@dataclass(frozen=True)
class Classification:
    domain: str
    specialization: str


def classify(title: str, categories: list[str] | None = None, description: str | None = None) -> Classification:
    """Pick the best specialization; title signals dominate description noise."""
    scores: dict[str, float] = {}
    ft = fold(title)
    fd = fold((description or "")[:2500])
    for cat in categories or []:
        fc = fold(cat)
        for hint, spec in _CATEGORY_HINTS.items():
            if re.search(r"(?<![a-z])" + re.escape(hint) + r"(?![a-z])", fc):
                scores[spec] = scores.get(spec, 0) + 4
    for spec, pat in _spec_patterns().items():
        title_hits = len(pat.findall(ft))
        desc_hits = min(len(pat.findall(fd)), 4)
        if title_hits or desc_hits:
            scores[spec] = scores.get(spec, 0) + 6 * title_hits + 1 * desc_hits
    if not scores:
        return Classification("autres", "autre")
    best = max(scores.items(), key=lambda kv: kv[1])
    if best[1] < 2:
        return Classification("autres", "autre")
    return Classification(SPECIALIZATION_DOMAIN[best[0]], best[0])


# -------------------------------------------------------------- contracts --
CONTRACT_TYPES: dict[str, str] = {
    "cdi": "CDI",
    "cdd": "CDD",
    "sivp": "SIVP",
    "stage": "Stage",
    "alternance": "Alternance",
    "freelance": "Freelance",
    "interim": "Intérim",
    "temps-partiel": "Temps partiel",
    "saisonnier": "Saisonnier",
}

_CONTRACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cdi", re.compile(r"\bcdi\b|contrat a duree indeterminee|permanent contract|\bpermanent\b|duree indeterminee")),
    ("cdd", re.compile(r"\bcdd\b|contrat a duree determinee|fixed[- ]term|duree determinee|\btemporary\b|\bcontractuel")),
    ("sivp", re.compile(r"\bsivp\b|contrat karama|\bkarama\b")),
    ("stage", re.compile(r"\bstage\b|\bstagiaire\b|\binternship\b|\bintern\b|\bpfe\b|\bpfa\b")),
    ("alternance", re.compile(r"\balternance\b|apprentissage|apprenticeship")),
    ("freelance", re.compile(r"\bfreelance\b|\bfree-lance\b|independant|\bcontractor\b|\bcontract\b|\bconsultant externe")),
    ("interim", re.compile(r"\binterim\b|\btemp agency")),
    ("temps-partiel", re.compile(r"temps partiel|part[- ]time|mi-temps")),
    ("saisonnier", re.compile(r"saisonnier|seasonal")),
]


def normalize_contracts(values: list[str] | str | None) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    found: list[str] = []
    for v in values:
        fv = fold(v).replace("_", " ")
        for slug, pat in _CONTRACT_PATTERNS:
            if pat.search(fv) and slug not in found:
                found.append(slug)
    return found


# -------------------------------------------------------------- workplace --
WORKPLACES = {"sur-site": "Sur site", "hybride": "Hybride", "teletravail": "Télétravail"}


def normalize_workplace(raw: str | None, *texts: str | None) -> str | None:
    """Explicit field first; then cautious inference from title/location text."""
    f = fold(raw).replace("_", " ").replace("-", " ")
    if f:
        if "hybri" in f:
            return "hybride"
        if any(k in f for k in ("remote", "teletravail", "telecommute", "a distance", "full remote", "work from home")):
            return "teletravail"
        if any(k in f for k in ("on site", "onsite", "sur site", "presentiel", "in office", "office")):
            return "sur-site"
    for t in texts:
        ft = fold(t)
        if not ft:
            continue
        if re.search(r"\bhybrid[e]?\b", ft):
            return "hybride"
        if re.search(r"\b(remote|full remote|teletravail|100% a distance|telecommute)\b", ft):
            return "teletravail"
    return None


# ------------------------------------------------------------- experience --
EXPERIENCE_LEVELS: dict[str, str] = {
    "debutant": "Débutant (0–1 an)",
    "junior": "Junior (1–3 ans)",
    "confirme": "Confirmé (3–5 ans)",
    "senior": "Senior (5–10 ans)",
    "expert": "Expert (10 ans et +)",
}


def _level_for_years(years: float) -> str:
    if years < 1:
        return "debutant"
    if years < 3:
        return "junior"
    if years < 5:
        return "confirme"
    if years < 10:
        return "senior"
    return "expert"


_YEARS_RANGE = re.compile(r"(?:entre|between|de|from)?\s*(\d{1,2})\s*(?:ans?|years?)?\s*(?:et|a|to|-|–)\s*(\d{1,2})\s*(?:ans?|years?)")
_YEARS_MIN = re.compile(r"(?:au moins|minimum|min\.?|at least|plus de|more than|over)?\s*(\d{1,2})\s*(?:\+|ans? et plus|ans? ou plus)?\s*(?:ans?|years?|yrs?)\b(?:\s*(?:d'|of\s+|de\s+)?(?:experience|exp))?")


def parse_experience(raw: str | None, title: str | None = None, description: str | None = None) -> tuple[str | None, float | None]:
    """Return (level slug, minimum years).  Explicit field > title > description."""
    f = fold(raw)
    if f:
        if re.search(r"\b(aucune|sans experience|debutant|no experience|0 an|moins d'un an|moins d'1 an|entry level|fresh graduate|jeune diplome)\b", f):
            return "debutant", 0.0
        m = _YEARS_RANGE.search(f)
        if m:
            lo = float(m.group(1))
            return _level_for_years(lo), lo
        m = _YEARS_MIN.search(f)
        if m:
            years = float(m.group(1))
            return _level_for_years(years), years
        lvl = _level_from_words(f)
        if lvl:
            return lvl, None
    ft = fold(title)
    lvl = _level_from_words(ft)
    if lvl:
        return lvl, None
    fd = fold((description or "")[:4000])
    if fd:
        m = re.search(r"(?:experience|expérience)[^.\n]{0,60}?(\d{1,2})\s*(?:\+\s*)?(?:ans?|years?)|(\d{1,2})\s*(?:\+\s*)?(?:ans?|years?)\s*(?:minimum\s*)?(?:d'|of\s+|de\s+)?(?:solide\s+|proven\s+|relevant\s+|professional\s+)?(?:experience)", fd)
        if m:
            years = float(m.group(1) or m.group(2))
            if years <= 25:
                return _level_for_years(years), years
        if re.search(r"\b(jeune diplome|jeunes diplomes|debutant accepte|debutants acceptes|fresh graduate|entry level|sans experience)\b", fd):
            return "debutant", 0.0
    return None, None


def _level_from_words(f: str) -> str | None:
    if not f:
        return None
    if re.search(r"\b(stagiaire|intern|internship|debutant|junior|jr)\b", f):
        return "debutant" if re.search(r"\b(stagiaire|intern|internship|debutant)\b", f) else "junior"
    if re.search(r"\b(expert|principal|staff engineer|directeur|director|head of)\b", f):
        return "expert"
    if re.search(r"\b(senior|sr|lead|confirme senior)\b", f):
        return "senior"
    if re.search(r"\b(confirme|confirmee|experimente|experimentee|mid[- ]level|intermediate)\b", f):
        return "confirme"
    return None
