from typing import Dict
from bidmatch.parsers.dscp import parse_dscp
from bidmatch.parsers.procure import parse_procure


def parse_article(html: str, source_code: str) -> Dict[str, str]:
    if source_code.lower() == "dscp":
        return parse_dscp(html)
    return parse_procure(html)
