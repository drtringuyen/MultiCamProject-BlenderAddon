"""TexOverride: ALB_ / NOR_ each on Bake (made into 01.Bake) or Load (the finished file in
02.Overrides). Its rows sit in 07 Generate Normal and its switches in the Cameras panel's
bake row (baking/ui.py); the modes themselves are object data the bakes, Export and the
work window follow (core.py)."""
from . import operators


def register():
    operators.register()


def unregister():
    operators.unregister()
