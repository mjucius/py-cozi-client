"""
Recipes support for the Cozi client. (Filled in during the 3.0 domain phase.)
"""

from ._transport import Transport


class RecipesMixin(Transport):
    """Recipes endpoints. Mixed into CoziClient; uses Transport._request."""
