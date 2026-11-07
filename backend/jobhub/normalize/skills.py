"""Required-skills extraction.

Sources rarely provide structured skills, so we match a curated dictionary
of canonical skills (with aliases) against the title and description.  Skills
explicitly provided by a source are always kept.  This is deterministic
keyword matching, not ML inference.
"""
from __future__ import annotations

import re
from functools import lru_cache

from .text import fold

# canonical name -> aliases (the canonical name itself is always an alias)
SKILLS: dict[str, list[str]] = {
    # languages & frameworks
    "Python": [], "Java": [], "JavaScript": ["js", "javascript es6"], "TypeScript": [], "C#": ["c sharp"],
    "C++": ["cpp"], "C": ["langage c"], "PHP": [], "Go": ["golang"], "Kotlin": [], "Swift": [], "Ruby": [], "Rust": [],
    "Scala": [], "R": ["langage r"], "Dart": [], "SQL": [], "PL/SQL": ["plsql"], "Bash": ["shell scripting"],
    ".NET": ["dotnet", "asp.net", ".net core", "asp.net core"], "Spring Boot": ["spring", "springboot"],
    "Django": [], "Flask": [], "FastAPI": [], "Node.js": ["nodejs", "node js", "node"], "Express.js": ["express.js", "expressjs"],
    "NestJS": ["nest.js"], "Laravel": [], "Symfony": [], "React": ["reactjs", "react.js"], "React Native": [],
    "Angular": ["angularjs"], "Vue.js": ["vuejs", "vue"], "Next.js": ["nextjs"], "Flutter": [], "HTML/CSS": ["html", "css", "html5", "css3"],
    "Tailwind CSS": ["tailwind"], "jQuery": [], "WordPress": [], "Odoo": [], "Android": [], "iOS": [],
    "GraphQL": [], "REST API": ["api rest", "rest apis", "restful", "web services rest"], "Microservices": ["microservice", "micro-services"],
    # data & AI
    "Machine Learning": ["apprentissage automatique"], "Deep Learning": [], "NLP": ["traitement du langage naturel"],
    "Computer Vision": ["vision par ordinateur"], "LLM": ["llms", "large language models"], "TensorFlow": [], "PyTorch": [],
    "scikit-learn": ["sklearn"], "Pandas": [], "NumPy": [], "Spark": ["apache spark", "pyspark"], "Hadoop": [], "Kafka": ["apache kafka"],
    "Airflow": ["apache airflow"], "Power BI": ["powerbi"], "Tableau": [], "Qlik": ["qlikview", "qlik sense"], "Talend": [],
    "ETL": [], "Data Warehouse": ["datawarehouse", "entrepot de donnees"], "Snowflake": [], "Databricks": [], "dbt": [],
    "MySQL": [], "PostgreSQL": ["postgres"], "Oracle Database": ["oracle db", "base oracle", "oracle"], "SQL Server": ["mssql", "ms sql"],
    "MongoDB": ["mongo"], "Redis": [], "Elasticsearch": ["elastic search", "elk"],
    # cloud / devops / security
    "AWS": ["amazon web services"], "Azure": ["microsoft azure"], "Google Cloud": ["gcp"], "Docker": [], "Kubernetes": ["k8s"],
    "Terraform": [], "Ansible": [], "Jenkins": [], "GitLab CI": ["gitlab-ci", "gitlab"], "GitHub Actions": [], "CI/CD": ["ci cd"],
    "Git": [], "Linux": ["unix"], "Windows Server": [], "VMware": [], "Active Directory": [], "Cisco": ["ccna", "ccnp"],
    "Fortinet": ["fortigate"], "SIEM": [], "ISO 27001": [], "Pentest": ["penetration testing", "tests d'intrusion"],
    "Dynatrace": [], "Prometheus": [], "Grafana": [],
    # methods & tools
    "Agile": ["methode agile", "methodologie agile", "agilite"], "Scrum": [], "Kanban": [], "Jira": [], "Confluence": [],
    "UML": [], "Selenium": [], "Cypress": [], "JUnit": [], "Postman": [], "Figma": [], "Adobe Photoshop": ["photoshop"],
    "Adobe Illustrator": ["illustrator"], "Adobe Premiere": ["premiere pro"], "InDesign": [], "Canva": [],
    # business / ERP / office
    "SAP": ["sap s/4hana", "sap fi", "sap mm", "sap sd"], "Salesforce": [], "Microsoft Dynamics": ["dynamics 365", "navision"],
    "Sage": ["sage 100", "sage saari"], "Microsoft Excel": ["excel", "ms excel"], "Microsoft Office": ["ms office", "pack office", "suite office", "office 365"],
    "CRM": [], "ERP": [], "SEO": ["referencement naturel"], "SEA": ["google ads"], "Google Analytics": [],
    "Marketing digital": ["digital marketing"], "Social media": ["reseaux sociaux"], "IFRS": ["normes ifrs"],
    "Comptabilité générale": ["comptabilite generale"], "Fiscalité": ["fiscalite"], "Contrôle de gestion": ["controle de gestion"],
    "Audit": [], "Gestion de projet": ["project management", "gestion des projets"], "PMP": [], "Lean": ["lean manufacturing", "lean six sigma"],
    "Six Sigma": [], "ISO 9001": [], "HACCP": [], "AutoCAD": [], "CATIA": [], "SolidWorks": [], "Revit": [], "MATLAB": [],
    "PLC / Automates": ["automates programmables", "plc", "siemens tia portal", "tia portal", "step 7"], "GMAO": [],
    "Négociation": ["negociation", "negotiation"], "Prospection": ["prospection commerciale"], "Recrutement": ["recruitment"],
    "Paie": ["payroll", "gestion de la paie"],
    # languages
    "Français": ["francais", "french", "langue francaise"], "Anglais": ["anglais", "english", "langue anglaise"],
    "Arabe": ["arabe", "arabic"], "Allemand": ["allemand", "german"], "Italien": ["italien", "italian"],
    "Espagnol": ["espagnol", "spanish"],
}

# Aliases that are ordinary words in prose; only trusted in titles or explicit skill lists.
_AMBIGUOUS = {"c", "r", "go", "node", "swift", "ruby", "rust", "dart", "spring", "express.js", "vue", "oracle", "lean",
              "audit", "sage", "crm", "erp", "sea", "android", "ios", "spark", "canva", "gitlab", "js", "unix", "git", "plc"}


@lru_cache(maxsize=1)
def _patterns() -> list[tuple[str, re.Pattern[str], bool]]:
    out = []
    for canon, aliases in SKILLS.items():
        for alias in {canon, *aliases}:
            a = fold(alias)
            pat = re.compile(r"(?<![a-z0-9+#./])" + re.escape(a) + r"(?![a-z0-9+#]|\.[a-z])")
            out.append((canon, pat, a in _AMBIGUOUS))
    out.sort(key=lambda x: -len(x[1].pattern))
    return out


def extract_skills(title: str | None, description: str | None, explicit: list[str] | None = None, limit: int = 20) -> list[str]:
    found: dict[str, int] = {}

    def scan(text: str, base: int, allow_ambiguous: bool) -> None:
        for canon, pat, ambiguous in _patterns():
            if ambiguous and not allow_ambiguous:
                continue
            m = pat.search(text)
            if m and (canon not in found or found[canon] > base + m.start()):
                found[canon] = base + m.start()

    # Explicit list from the source: keep as-is (normalized to canonical when known).
    explicit_out: list[str] = []
    for s in explicit or []:
        s_clean = s.strip()
        if not s_clean:
            continue
        fs = fold(s_clean)
        canon = next((c for c, pat, _ in _patterns() if pat.fullmatch(fs)), None)
        name = canon or s_clean[:60]
        if name not in explicit_out:
            explicit_out.append(name)

    if title:
        scan(fold(title), 0, True)
    if description:
        scan(fold(description)[:12000], 1000, False)
    ordered = explicit_out + [c for c, _ in sorted(found.items(), key=lambda kv: kv[1]) if c not in explicit_out]
    return ordered[:limit]
