import importlib
import logging
import pkgutil
from pathlib import Path

logger = logging.getLogger(__name__)


class Skill:
    name: str = ""
    triggers: list[str] = []
    description: str = ""
    research_output: bool = False  # True → result injected into LLM as context, not spoken directly

    def can_handle(self, intent: str) -> bool:
        return any(t.lower() in intent.lower() for t in self.triggers)

    def execute(self, params: dict) -> str:
        raise NotImplementedError


def load_all_skills(config: dict) -> list[Skill]:
    """Auto-discover and instantiate all Skill subclasses in this package."""
    skills_dir = Path(__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(skills_dir)]):
        if module_name.startswith("_"):
            continue
        try:
            importlib.import_module(f"skills.{module_name}")
        except Exception as e:
            logger.warning("Could not load skill module '%s': %s", module_name, e)

    instances: list[Skill] = []
    for cls in Skill.__subclasses__():
        try:
            instance = cls(config)
            instances.append(instance)
            logger.debug("Loaded skill: %s", cls.__name__)
        except Exception as e:
            logger.warning("Could not instantiate skill '%s': %s", cls.__name__, e)

    logger.info("Skills loaded: %d", len(instances))
    return instances
