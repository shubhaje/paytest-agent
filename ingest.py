"""ingest.py - Week 2, step 1: turn ISO 20022 XSD files into retrievable chunks.

What it does
  1. Reads every *.xsd file in the data/ folder.
  2. Splits each file into one record per named type (complexType / simpleType).
  3. Merges identical types shared by several messages: stored once, tagged
     with every message that contains them.
  4. Writes data/chunks.json with TWO text versions of every chunk:
       raw_text      = the XML of the type exactly as it appears in the file  (baseline)
       readable_text = plain-English rendering with the constraints spelled out (improved)

Usage (project root, venv active):
    pip install lxml
    python ingest.py                         # build data/chunks.json + print a summary
    python ingest.py --stats                 # per-file counts only (check them against the XSD)
    python ingest.py --show GroupHeader131   # print both text versions of one type

This script is an offline step. It does not call any API and it is not part of
the Docker image. Embedding and the vector store come in the next step.
"""

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

from lxml import etree

XS = "{http://www.w3.org/2001/XMLSchema}"

# --------------------------------------------------------------------------
# Starter glossary: ISO 20022 abbreviation -> words.
# Used ONLY to build readable_text. This is a hand-made starter list, so verify
# each entry against the ISO 20022 message definitions before trusting it, and
# extend it whenever you find a name that is not expanded.
# --------------------------------------------------------------------------
GLOSSARY = {
    "Msg": "Message", "Id": "Identification", "Cre": "Creation", "Dt": "Date",
    "Tm": "Time", "Nb": "Number", "Txs": "Transactions", "Tx": "Transaction",
    "Ctrl": "Control", "Tot": "Total", "Intr": "Inter", "Bk": "Bank",
    "Sttlm": "Settlement", "Amt": "Amount", "Ccy": "Currency",
    "Eqvt": "Equivalent", "Instd": "Instructed", "Instg": "Instructing",
    "Instr": "Instruction", "Agt": "Agent", "Dbtr": "Debtor", "Cdtr": "Creditor",
    "Dbt": "Debit", "Cdt": "Credit", "Trf": "Transfer", "Cstmr": "Customer",
    "Acct": "Account", "Rmt": "Remittance", "Inf": "Information",
    "Pmt": "Payment", "Tp": "Type", "Prtry": "Proprietary", "Cd": "Code",
    "Othr": "Other", "Ref": "Reference", "Addtl": "Additional", "Adr": "Address",
    "Pstl": "Postal", "Ctry": "Country", "Nm": "Name", "Orgnl": "Original",
    "Rtr": "Return", "Rsn": "Reason", "Chrg": "Charge", "Chrgs": "Charges",
    "Br": "Bearer", "Xchg": "Exchange", "Prty": "Priority",
    "Indctn": "Indication", "Req": "Request", "Fin": "Financial",
    "Instn": "Institution", "FI": "Financial Institution", "Grp": "Group",
    "Hdr": "Header", "Clr": "Clearing", "Sys": "System", "Mmb": "Member",
    "Svc": "Service", "Lvl": "Level", "Purp": "Purpose", "Issr": "Issuer",
    "Schme": "Scheme", "Ultmt": "Ultimate", "Intrmy": "Intermediary",
    "Prvs": "Previous", "Strd": "Structured", "Ustrd": "Unstructured",
    "Rltd": "Related", "Pty": "Party", "Org": "Organisation", "Prvt": "Private",
    "Mtd": "Method", "Twn": "Town", "Pst": "Post", "Dvsn": "Division",
    "Bldg": "Building", "Flr": "Floor", "Dept": "Department", "Strt": "Street",
    "Orgtr": "Originator", "Authrty": "Authority", "Rgltry": "Regulatory",
    "Rptg": "Reporting", "Dtls": "Details", "Dtl": "Detail", "Fr": "From",
    "Rcvg": "Receiving", "Frqcy": "Frequency", "Prd": "Period", "Yr": "Year",
    "Lcl": "Local", "Instrm": "Instrument", "Mndt": "Mandate",
    "Ctct": "Contact", "Phne": "Phone", "Mob": "Mobile",
    "UETR": "Unique End-to-end Transaction Reference",
    "BICFI": "BIC (Business Identifier Code) Financial Institution",
    "BIC": "BIC (Business Identifier Code)",
    "LEI": "Legal Entity Identifier",
}


# --------------------------------------------------------------------------
# Step 1: read one XSD file
# --------------------------------------------------------------------------
def message_id(root, path):
    """Message name such as pacs.008.001.14, taken from targetNamespace."""
    last = root.get("targetNamespace", "").rsplit(":", 1)[-1]
    if re.fullmatch(r"[a-z]+\.\d{3}\.\d{3}\.\d{2}", last):
        return last
    return path.stem.replace("_", ".")  # fallback: the file name


def clean_xml(node):
    """XML text of one type: no namespace declarations, LF endings, 2-space indent.

    Removing the xmlns declarations matters: they differ per message, and
    leaving them in would make identical types look different.
    """
    text = etree.tostring(node, pretty_print=True, encoding="unicode")
    text = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", text)
    return text.replace("\r\n", "\n").strip()


def parse_simple(node):
    """Facts about a simpleType (a restricted string, number or date)."""
    info = {"base": "unknown", "enum": [], "pattern": None, "min_len": None,
            "max_len": None, "digits": None, "fraction": None, "min_value": None}
    restriction = node.find(XS + "restriction")
    if restriction is None:
        return info
    info["base"] = restriction.get("base", "unknown").replace("xs:", "")
    for facet in restriction:
        tag = facet.tag.replace(XS, "")
        value = facet.get("value")
        if tag == "enumeration":
            info["enum"].append(value)
        elif tag == "pattern":
            info["pattern"] = value
        elif tag == "minLength":
            info["min_len"] = int(value)
        elif tag == "maxLength":
            info["max_len"] = int(value)
        elif tag == "totalDigits":
            info["digits"] = int(value)
        elif tag == "fractionDigits":
            info["fraction"] = int(value)
        elif tag == "minInclusive":
            info["min_value"] = value
    return info


def parse_complex(node):
    """Fields of a complexType.

    kind is one of:
      sequence      - fields appear in order; each is mandatory or optional
      choice        - exactly one of the listed alternatives is used
      simpleContent - a value (such as an amount) plus attributes (such as Ccy)
    """
    extension = node.find(f"{XS}simpleContent/{XS}extension")
    if extension is not None:
        attributes = [{"name": a.get("name"), "type": a.get("type"),
                       "required": a.get("use") == "required"}
                      for a in extension.findall(XS + "attribute")]
        return {"kind": "simpleContent", "base": extension.get("base"),
                "attributes": attributes, "fields": []}

    kind = "sequence"
    compositor = node.find(XS + "sequence")
    if compositor is None:
        compositor = node.find(XS + "choice")
        kind = "choice"

    fields = []
    if compositor is not None:
        for el in compositor.findall(XS + "element"):
            # XSD rule: if minOccurs is absent the default is 1 = MANDATORY.
            required = el.get("minOccurs", "1") != "0"
            max_raw = el.get("maxOccurs", "1")
            max_occurs = None if max_raw == "unbounded" else int(max_raw)  # None = no limit
            fields.append({"name": el.get("name") or el.get("ref"),
                           "type": el.get("type"),
                           "required": required,
                           "max_occurs": max_occurs})
    return {"kind": kind, "fields": fields, "attributes": []}


def load_xsd(path):
    """Return (message_id, [one record per named type]) for a single XSD file."""
    parser = etree.XMLParser(remove_blank_text=True)
    root = etree.parse(str(path), parser).getroot()
    msg = message_id(root, path)
    records = []
    for node in root:
        if node.tag == XS + "simpleType":
            kind, detail = "simple", parse_simple(node)
        elif node.tag == XS + "complexType":
            kind, detail = "complex", parse_complex(node)
        else:
            continue  # comments and the top-level <xs:element name="Document">
        records.append({"name": node.get("name"), "message": msg, "kind": kind,
                        "detail": detail, "xml": clean_xml(node)})
    return msg, records


# --------------------------------------------------------------------------
# Step 2: merge identical types across files
# --------------------------------------------------------------------------
def merge_types(records):
    """Same name AND same XML -> one chunk listing every message that has it."""
    groups = {}
    for rec in records:
        groups.setdefault((rec["name"], rec["xml"]), []).append(rec)

    variants = Counter(name for name, _ in groups)  # versions of each name
    chunks = []
    for (name, xml), recs in sorted(groups.items()):
        messages = sorted({r["message"] for r in recs})
        # If one name has several different definitions (only "Document" here),
        # the id gets the first message name so ids stay unique.
        chunk_id = name if variants[name] == 1 else f"{name}@{messages[0]}"
        chunks.append({"id": chunk_id, "name": name, "kind": recs[0]["kind"],
                       "messages": messages, "detail": recs[0]["detail"],
                       "raw_text": xml})
    return chunks


def add_used_in(chunks):
    """For each type, the names of the complex types that reference it."""
    refs = {}
    for chunk in chunks:
        if chunk["kind"] != "complex":
            continue
        detail = chunk["detail"]
        used = [f["type"] for f in detail["fields"]]
        used += [a["type"] for a in detail["attributes"]]
        used.append(detail.get("base"))
        for type_name in set(u for u in used if u):
            refs.setdefault(type_name, set()).add(chunk["name"])
    for chunk in chunks:
        chunk["used_in"] = sorted(refs.get(chunk["name"], []))


# --------------------------------------------------------------------------
# Step 3: build the readable (improved) text
# --------------------------------------------------------------------------
def expand_name(name):
    """IntrBkSttlmAmt -> 'Inter Bank Settlement Amount' (approximate, uses GLOSSARY)."""
    tokens = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|\d+|[a-z]+", name)
    return " ".join(GLOSSARY.get(t, t) for t in tokens)


def describe_simple(info):
    """One line of constraints, e.g. 'string; 1 to 35 characters'."""
    parts = [info["base"]]
    if info["enum"]:
        parts.append("one of: " + ", ".join(info["enum"]))
    low, high = info["min_len"], info["max_len"]
    if low is not None and high is not None:
        parts.append(f"exactly {low} characters" if low == high
                     else f"{low} to {high} characters")
    elif high is not None:
        parts.append(f"up to {high} characters")
    elif low is not None:
        parts.append(f"at least {low} characters")
    if info["pattern"]:
        parts.append(f"must match pattern {info['pattern']}")
    if info["digits"] is not None:
        parts.append(f"up to {info['digits']} digits in total")
    if info["fraction"] is not None:
        parts.append(f"up to {info['fraction']} decimal places")
    if info["min_value"] is not None:
        parts.append(f"minimum value {info['min_value']}")
    return "; ".join(parts)


def type_summary(type_name, lookup):
    """Inline description of a field type, so a chunk stands on its own."""
    if not type_name:
        return "unknown type"
    if type_name.startswith("xs:"):
        return type_name.replace("xs:", "")
    target = lookup.get(type_name)
    if target is None:
        return type_name
    if target["kind"] == "simple":
        return f"{type_name} [{describe_simple(target['detail'])}]"
    return f"{type_name} (structured type, see its own entry)"


def label(name):
    """'Cd (Code)' - the XML name plus its expansion, when they differ."""
    words = expand_name(name)
    return name if words == name else f"{name} ({words})"


def occurrence_text(field):
    if field["max_occurs"] is None:
        return "repeatable, no limit"
    if field["max_occurs"] > 1:
        return f"repeatable, at most {field['max_occurs']} times"
    return "single"


def build_readable(chunk, lookup):
    detail = chunk["detail"]
    kind_word = "simple type" if chunk["kind"] == "simple" else "structured type"
    lines = [f"ISO 20022 {kind_word} {chunk['name']}",
             "Used in messages: " + ", ".join(chunk["messages"])]
    if chunk["used_in"]:
        shown = chunk["used_in"][:10]
        extra = len(chunk["used_in"]) - len(shown)
        more = f" (+{extra} more)" if extra else ""
        lines.append("Referenced by types: " + ", ".join(shown) + more)

    if chunk["kind"] == "simple":
        lines.append("Constraints: " + describe_simple(detail))
    elif detail["kind"] == "simpleContent":
        lines.append(f"A value of type {type_summary(detail['base'], lookup)} "
                     "with these attributes:")
        for attr in detail["attributes"]:
            need = "required" if attr["required"] else "optional"
            lines.append(f"- {label(attr['name'])}: type "
                         f"{type_summary(attr['type'], lookup)}; {need}")
    else:
        if detail["kind"] == "choice":
            lines.append("Structure: choice - exactly one of these alternatives is used.")
        else:
            lines.append("Structure: sequence - fields appear in this order.")
        lines.append("Fields:")
        for f in detail["fields"]:
            if detail["kind"] == "choice":
                need = "alternative"
            else:
                need = "mandatory" if f["required"] else "optional"
            lines.append(f"- {label(f['name'])}: type {type_summary(f['type'], lookup)}; "
                         f"{need}; {occurrence_text(f)}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Step 4: self-check - does readable_text agree with the raw XML?
# --------------------------------------------------------------------------
def check_chunks(chunks):
    """Return a list of problems. Empty list means every chunk passed."""
    problems = []
    for c in chunks:
        raw, text = c["raw_text"], c["readable_text"]
        if c["kind"] == "complex":
            expected = len(re.findall(r"<xs:(?:element|attribute)\b", raw))
            bullets = sum(1 for line in text.splitlines() if line.startswith("- "))
            if expected != bullets:
                problems.append(f"{c['id']}: XML has {expected} fields, text lists {bullets}")
            req_xml = len(re.findall(r"<xs:element\b(?![^>]*minOccurs=\"0\")", raw))
            req_txt = text.count("; mandatory;")
            if c["detail"]["kind"] == "sequence" and req_xml != req_txt:
                problems.append(f"{c['id']}: XML has {req_xml} mandatory fields, text says {req_txt}")
        else:
            for value in re.findall(r'<xs:(?:enumeration|pattern) value="([^"]*)"', raw):
                if html.unescape(value) not in text:
                    problems.append(f"{c['id']}: value '{value}' missing from text")
    return problems


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def print_stats(file_rows):
    print(f"{'file':<24}{'message':<20}{'complex':>8}{'simple':>8}")
    for row in file_rows:
        print(f"{row['file']:<24}{row['message']:<20}{row['complex']:>8}{row['simple']:>8}")


def main():
    parser = argparse.ArgumentParser(description="Turn ISO 20022 XSD files into chunks.")
    parser.add_argument("--data-dir", default="data", help="folder with the .xsd files")
    parser.add_argument("--out", default=None, help="output file (default: <data-dir>/chunks.json)")
    parser.add_argument("--stats", action="store_true", help="print per-file counts and stop")
    parser.add_argument("--show", metavar="TYPE", help="print one type (raw + readable) and stop")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    files = sorted(data_dir.glob("*.xsd"))
    if not files:
        sys.exit(f"No .xsd files found in '{data_dir}'. Put the XSD files there first.")

    records, file_rows = [], []
    for path in files:
        try:
            msg, recs = load_xsd(path)
        except etree.XMLSyntaxError as exc:
            sys.exit(f"Could not parse {path.name}: {exc}")
        records.extend(recs)
        file_rows.append({"file": path.name, "message": msg,
                          "complex": sum(r["kind"] == "complex" for r in recs),
                          "simple": sum(r["kind"] == "simple" for r in recs)})

    if args.stats:
        print_stats(file_rows)
        return

    chunks = merge_types(records)
    add_used_in(chunks)
    lookup = {c["name"]: c for c in chunks}
    for c in chunks:
        c["readable_text"] = build_readable(c, lookup)

    if args.show:
        matches = [c for c in chunks if c["name"] == args.show]
        if not matches:
            sys.exit(f"No type named '{args.show}'. Names are case-sensitive.")
        for c in matches:
            print(f"===== {c['id']}: RAW XML =====\n{c['raw_text']}\n")
            print(f"===== {c['id']}: READABLE TEXT =====\n{c['readable_text']}\n")
        return

    problems = check_chunks(chunks)

    out_file = Path(args.out) if args.out else data_dir / "chunks.json"
    payload = {
        "source_files": [r["file"] for r in file_rows],
        "chunks": [{k: c[k] for k in ("id", "name", "kind", "messages", "used_in",
                                      "raw_text", "readable_text")} for c in chunks],
    }
    with open(out_file, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    print_stats(file_rows)
    total = sum(r["complex"] + r["simple"] for r in file_rows)
    shared = sum(len(c["messages"]) > 1 for c in chunks)
    print(f"\nTypes read: {total} | unique chunks: {len(chunks)} | "
          f"chunks shared by 2+ messages: {shared}")
    print(f"Wrote {out_file}")
    if problems:
        print(f"\nSELF-CHECK FOUND {len(problems)} PROBLEM(S):")
        for p in problems[:20]:
            print("  -", p)
        sys.exit(1)
    print(f"Self-check passed: readable text matches the raw XML for all {len(chunks)} chunks.")


if __name__ == "__main__":
    main()