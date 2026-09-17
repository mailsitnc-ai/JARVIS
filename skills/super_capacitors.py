# Evolved by JARVIS on 2026-09-17 13:54 for: super capacitors
SKILL = {'name': 'super_capacitors', 'description': 'Provide a concise explanation of super capacitors, including definition, operation principle, typical uses, and advantages over batteries.', 'triggers': ['\\bsuper\\s*capacitors\\b'], 'version': 1, 'origin': 'evolved'}


def run(request: str, context: dict) -> str:
    # The skill is triggered for any request mentioning super capacitors,
    # so we return a ready‑made short explanation.
    explanation = (
        "Super capacitors (also called ultracapacitors or electrochemical capacitors) are energy storage devices that bridge the gap between traditional capacitors and batteries. "
        "They store energy by separating charge in an electric double‑layer at the interface of a high‑surface‑area electrode and an electrolyte, sometimes supplemented by pseudocapacitive faradaic reactions. "
        "Because they can charge and discharge very quickly (seconds to minutes) and endure millions of cycles, they are used for applications such as regenerative braking in vehicles, power‑backup for electronics, load‑leveling in renewable‑energy systems, and short‑term energy bursts in tools and drones. "
        "Compared to batteries, super capacitors have higher power density, longer cycle life, and operate over a wider temperature range, though they have lower energy density and higher self‑discharge."
    )
    return explanation
