from datetime import datetime

import pytest

from jobhub.collectors.base import RawJob
from jobhub.normalize import Rejected, company_key, fingerprint, normalize, split_recruits_title
from jobhub.normalize.locations import is_tunisia, resolve_location
from jobhub.normalize.skills import extract_skills
from jobhub.normalize.taxonomy import classify, normalize_contracts, normalize_workplace, parse_experience
from jobhub.normalize.text import html_to_text, parse_date, sanitize_html


@pytest.mark.parametrize("text,gov,city", [
    ("Tunis, Tunisie", "tunis", "Tunis"),
    ("Hammam Zriba, Zaghouan, Tunisie", "zaghouan", "Hammam Zriba"),
    ("Hammam Sousse", "sousse", "Hammam Sousse"),
    ("Sousse, Sousse, Tunisia", "sousse", "Sousse"),
    ("Les Berges du Lac 2", "tunis", "Les Berges du Lac"),
    ("Technopole El Ghazala", "ariana", "Raoued"),
    ("Gabès", "gabes", "Gabès"),
    ("Djerba Midoun", "medenine", "Djerba"),
    ("Chenini, Tataouine Governorate, Tunisia", "tataouine", "Chenini"),
    ("Ben Arous", "ben-arous", "Ben Arous"),
    ("Gouvernorat de Nabeul", "nabeul", "Nabeul"),
    ("Tunisie", None, None),
    ("Paris, France", None, None),
])
def test_resolve_location(text, gov, city):
    loc = resolve_location(text)
    assert (loc.governorate, loc.city) == (gov, city)


def test_tunisie_does_not_match_tunis_city():
    assert resolve_location("Tunisie").governorate is None


def test_is_tunisia():
    assert is_tunisia("TN") is True
    assert is_tunisia("Tunisia") is True
    assert is_tunisia("FR") is False
    assert is_tunisia(None, "Remote - Tunisia") is True
    assert is_tunisia(None, "Sfax") is True
    assert is_tunisia(None, "Algiers, Algeria") is None


@pytest.mark.parametrize("raw,expected", [
    (["CDI"], ["cdi"]),
    (["CDI, CDD, SIVP"], ["cdi", "cdd", "sivp"]),
    (["Full-time"], []),
    (["Contract"], ["freelance"]),
    (["Internship"], ["stage"]),
    (["fulltime_fixed_term"], ["cdd"]),
    (["CDD temps partiel"], ["cdd", "temps-partiel"]),
    (["FULL_TIME"], []),
])
def test_contracts(raw, expected):
    assert normalize_contracts(raw) == expected


@pytest.mark.parametrize("raw,title,level,years", [
    ("Entre 2 et 5 ans", None, "junior", 2),
    ("Moins d'un an", None, "debutant", 0),
    ("Plus de 10 ans", None, "expert", 10),
    ("5 ans", None, "senior", 5),
    (None, "Senior Backend Developer", "senior", None),
    (None, "Développeur Junior PHP", "junior", None),
    (None, "Stagiaire PFE", "debutant", None),
    (None, "Comptable", None, None),
])
def test_experience(raw, title, level, years):
    assert parse_experience(raw, title) == (level, years)


def test_experience_from_description():
    assert parse_experience(None, "Comptable", "Vous avez une expérience de 3 ans minimum en cabinet.") == ("confirme", 3)
    assert parse_experience(None, "Dev", "Minimum 3 years of experience in IT") == ("confirme", 3)


def test_workplace():
    assert normalize_workplace("Sur site") == "sur-site"
    assert normalize_workplace("on_site") == "sur-site"
    assert normalize_workplace("hybrid") == "hybride"
    assert normalize_workplace("TELECOMMUTE") == "teletravail"
    assert normalize_workplace(None, "Collections Specialist / Remote") == "teletravail"
    assert normalize_workplace(None, "Comptable") is None


@pytest.mark.parametrize("title,cats,domain,spec", [
    ("Développeur Full Stack Java/Angular", [], "informatique", "developpement-logiciel"),
    ("Data Scientist", [], "informatique", "data-ia"),
    ("Infrastructure & Security Officer", ["Banque / Assurances / Finance Marché"], "informatique", "cybersecurite"),
    ("Comptable", [], "finance-comptabilite", "comptabilite"),
    ("Technicien de maintenance", [], "ingenierie-industrie", "maintenance"),
    ("Téléconseiller francophone", [], "relation-client", "centre-appels"),
    ("Chef de Cuisine", [], "hotellerie-tourisme", "restauration"),
    ("Gestionnaire de Portefeuille", ["Finance"], "finance-comptabilite", "banque-assurance"),
    ("Zzz", [], "autres", "autre"),
    ("Des Téléconseillers en Réception d'appel Marché Canadien", [], "relation-client", "centre-appels"),
    ("Conseillère de ventes (h/f)", [], "commercial-vente", "vente-retail"),
    ("Un Agent Immobilier (H/F)", [], "commercial-vente", "immobilier"),
    ("Barista/Comptoiriste Café TUNISIA MALL 1", [], "hotellerie-tourisme", "restauration"),
    ("Agent(e) de facturation", [], "finance-comptabilite", "comptabilite"),
])
def test_classify(title, cats, domain, spec):
    c = classify(title, cats, "")
    assert (c.domain, c.specialization) == (domain, spec)


def test_skills():
    s = extract_skills("Développeur Python / Django", "Maîtrise de Docker, PostgreSQL et Git. Anglais courant. Go to market.")
    assert s[:2] == ["Python", "Django"]
    assert {"Docker", "PostgreSQL", "Anglais"} <= set(s)
    assert "Go" not in s  # ambiguous words only trusted in titles


def test_skills_keep_explicit():
    assert extract_skills("X", "", ["golang", "Négociation commerciale"])[:2] == ["Go", "Négociation commerciale"]


def test_split_recruits_title():
    assert split_recruits_title("Zitouna Tamkeen recrute Chef Service Reporting") == ("Zitouna Tamkeen", "Chef Service Reporting")
    assert split_recruits_title("Monoprix recrute des Caissières Polyvalentes") == ("Monoprix", "Caissières Polyvalentes")
    assert split_recruits_title("Ingénieur Production") == (None, "Ingénieur Production")


def test_company_key_and_fingerprint():
    assert company_key("BANK ABC") == company_key("Bank ABC Tunisie") == "bank abc"
    assert company_key("Sotubi S.A.") == "sotubi"
    assert fingerprint("Bank ABC", "Comptable (H/F)", "tunis") == fingerprint("BANK ABC SA", "comptable", "tunis")


def test_dates():
    assert parse_date("2026-11-07") == datetime(2026, 11, 7)
    assert parse_date("2026-08-12T09:54:03.492Z") == datetime(2026, 8, 12, 9, 54, 3, 492000)
    assert parse_date("Tue, 03 Nov 2026 05:03:59 +0000") == datetime(2026, 11, 3, 5, 3, 59)
    assert parse_date("16 octobre 2026") == datetime(2026, 10, 16)
    assert parse_date(1762300000000).year == 2025
    assert parse_date("n/a") is None


def test_sanitize_html_strips_scripts_and_attributes():
    out = sanitize_html('<div onclick="x()"><h1>Titre</h1><script>alert(1)</script><p style="c">Texte <a href="javascript:x">l</a>'
                        '<a href="https://ok.tn">ok</a></p><img src=x onerror=y></div>')
    assert "script" not in out and "onclick" not in out and "javascript" not in out and "img" not in out
    assert "<h3>Titre</h3>" in out
    assert 'href="https://ok.tn"' in out and 'rel="nofollow noopener"' in out


def test_html_to_text():
    assert html_to_text("<p>A</p><ul><li>x</li><li>y</li></ul>") == "A\n• x\n• y"


def _raw(**kw):
    base = dict(external_id="1", url="https://s.tn/1", title="Développeur Python", company_name="Acme SARL",
                description_html="<p>Python, Django, 3 ans d'expérience</p>", location_raw="Sfax", country="TN",
                contract_raw=["CDI"], published_at="2026-11-01")
    base.update(kw)
    return RawJob(**base)


def test_normalize_full():
    n = normalize(_raw(), now=datetime(2026, 11, 7))
    assert n.title == "Développeur Python"
    assert n.company_name == "Acme SARL" and n.company_key == "acme"
    assert (n.governorate, n.city, n.location_label) == ("sfax", "Sfax", "Sfax")
    assert n.contracts == ["cdi"]
    assert n.experience_level == "confirme"
    assert n.domain == "informatique"
    assert "Django" in n.skills
    assert n.apply_url == "https://s.tn/1"


def test_normalize_rejects_foreign_offers():
    with pytest.raises(Rejected):
        normalize(_raw(country="DZ", location_raw="Algiers"))
    with pytest.raises(Rejected):
        normalize(_raw(country=None, location_raw="Remote - EMEA"))


def test_tunisian_board_offer_abroad_is_rejected():
    # Keejob-style: no country field, the location text decides.
    with pytest.raises(Rejected):
        normalize(_raw(country=None, location_raw="Nouadhibou, Mauritanie"))
    with pytest.raises(Rejected):
        normalize(_raw(country="TN", location_raw="France, France"))  # board default must not override text
    assert normalize(_raw(country=None, location_raw="Tunis, Tunisie")).governorate == "tunis"


def test_date_only_expiry_lasts_the_whole_day():
    n = normalize(_raw(expires_at="2026-12-07"))
    assert n.expires_at == datetime(2026, 12, 7, 23, 59, 59)


def test_contract_inferred_from_description_acronyms_only():
    assert normalize(_raw(contract_raw=[], description_html="<p>Contrat : SIVP puis CDI</p>")).contracts == ["cdi", "sivp"]
    assert normalize(_raw(contract_raw=[], description_html="<p>Un stage de fin d'études a été réalisé</p>")).contracts == []


def test_normalize_future_date_clamped_and_confidential_company():
    n = normalize(_raw(published_at="2030-01-01", company_name="Confidentiel"), now=datetime(2026, 11, 7))
    assert n.published_at == datetime(2026, 11, 7)
    assert n.company_name is None
