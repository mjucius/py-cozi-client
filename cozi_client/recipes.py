"""
Recipes (Cozi Meals): the recipe box, curated recipe packs, import from a web page,
recipe photos, and adding a recipe's ingredients to a shopping list.

Every endpoint is under API version 2103, as in the web client (bundle main.c43180f9).
"""

import mimetypes
import re
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, NoReturn, Optional, Union

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ._transport import ID_PATTERN, Transport, _reason, logger, parse_model, parse_models, validate_id
from .exceptions import APIError, CoziException, CoziValidationError, ResponseParseError, WriteVerificationError

if TYPE_CHECKING:
    from .lists import CoziItem

RECIPES = "/2103/{acct}/food/recipe/"
SCRAPE = "/2103/{acct}/food/scrape/recipe/"
CURATED = "/2103/{acct}/food/curated/recipe/"
# The web client refuses larger photos before uploading (yxWn.tc = 2e7 bytes).
MAX_PHOTO_BYTES = 20_000_000


class CoziIngredient(BaseModel):
    """One ingredient line. Cozi keys ingredients by a uuid4 the client mints."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    ingredient_id: str = Field(alias="ingredientId")
    name: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _mint_id(cls, data: Any) -> Any:
        # A before-validator rather than a default_factory: the minted id then counts
        # as set, so it survives exclude_unset on the way back out. Scraped
        # ingredients arrive without one, as the web client's do.
        if isinstance(data, dict) and not (data.get("ingredientId") or data.get("ingredient_id")):
            data = {k: v for k, v in data.items() if k != "ingredient_id"}
            data["ingredientId"] = str(uuid.uuid4())
        return data


class CoziRecipe(BaseModel):
    """
    A recipe as Cozi stores it.

    Cozi has no partial update: an edit re-sends the whole object. Unknown keys are
    kept (``extra="allow"``) so that an edit round-trips everything that was read,
    including server-derived fields such as ``creationDate`` and ``sourceUrl``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    recipe_box_id: Optional[str] = Field(None, alias="recipeBoxId")
    recipe_id: Optional[str] = Field(None, alias="recipeId")
    name: Optional[str] = None
    description: Optional[str] = None
    ingredients: Optional[List[CoziIngredient]] = None
    instructions: Optional[str] = None
    notes: Optional[str] = None
    source_raw: Optional[str] = Field(None, alias="sourceRaw")
    # predicate -> names: has-folder, has-keyword, has-cuisine, in-curated-pack
    tags: Optional[Dict[str, Optional[List[str]]]] = None
    photos: Optional[List[Dict[str, Any]]] = None
    is_published: Optional[bool] = Field(None, alias="isPublished")
    recipe_source: Optional[str] = Field(None, alias="recipeSource")

    @property
    def categories(self) -> List[str]:
        """The user's categories: the has-folder and has-keyword tags."""
        tags = self.tags or {}
        return [*(tags.get("has-folder") or []), *(tags.get("has-keyword") or [])]

    def to_wire(self) -> Dict[str, Any]:
        """The stored object as read, plus every attribute assigned since."""
        return self.model_dump(by_alias=True, mode="json", exclude_unset=True)


def _lines(value: Union[str, List[str]], name: str) -> List[str]:
    """Non-blank lines, verbatim. A str is split on newlines, as the web's text box is."""
    if isinstance(value, str):
        value = re.split(r"\r?\n", value)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CoziValidationError(f"{name} must be a str or a list of str")
    return [v for v in value if v.strip()]


def _saveable(recipe: CoziRecipe) -> str:
    """The recipe_box_id of a recipe update_recipe may re-save, checked before any I/O."""
    box_id = validate_id(recipe.recipe_box_id, "recipe_box_id")
    if not isinstance(recipe.name, str) or not recipe.name.strip():
        raise CoziValidationError("Recipe name cannot be empty")
    # ponytail: heuristic guard (a recipe read from Cozi always carries these keys);
    # fetch-then-merge is the upgrade if callers need to build recipes by hand.
    if not {"ingredients", "instructions", "recipe_id"} & recipe.model_fields_set:
        # A hand-built CoziRecipe(recipe_box_id=..., name=...) would blank the
        # stored recipe: the endpoint replaces the whole object.
        raise CoziValidationError("update_recipe needs a recipe read from Cozi, not a hand-built one")
    return box_id


class RecipesMixin(Transport):
    """Recipes endpoints. Mixed into CoziClient; uses Transport._request."""

    async def get_recipes(self) -> List[CoziRecipe]:
        """Every recipe in the household's recipe box (there is no get-by-id)."""
        return parse_models(CoziRecipe, await self._request("GET", RECIPES))

    async def create_recipe(
        self,
        name: str,
        ingredients: Union[str, List[str]],
        instructions: str = "",
        description: str = "",
        notes: str = "",
        source: str = "",
        categories: Optional[List[str]] = None,
    ) -> CoziRecipe:
        """
        Add a recipe to the box and return it as stored.

        ``ingredients`` is a list of lines or one newline-separated block; blank
        lines are dropped. ``categories`` become has-folder tags. The body matches
        the web client's "Typed Into Cozi" save.
        """
        if not isinstance(name, str) or not name.strip():
            raise CoziValidationError("Recipe name cannot be empty")
        lines = _lines(ingredients, "ingredients")
        folders = _lines(categories or [], "categories")
        for label, value in (("instructions", instructions), ("description", description),
                             ("notes", notes), ("source", source)):
            if not isinstance(value, str):
                raise CoziValidationError(f"{label} must be a str")
        body = {
            "recipeSource": "Typed Into Cozi",
            "cookTimeText": None,
            "servingSize": None,
            "nutrition": None,
            "title": name,
            "name": name,
            "sourceRaw": source,
            "description": description,
            "notes": notes,
            "isPublished": False,
            "tags": {"has-folder": folders} if folders else {},
            "ingredients": [{"ingredientId": str(uuid.uuid4()), "name": line} for line in lines],
            "instructions": instructions,
        }
        return parse_model(CoziRecipe, await self._request("POST", RECIPES, json=body))

    async def update_recipe(self, recipe: CoziRecipe) -> CoziRecipe:
        """
        Save an edited recipe and return it as stored.

        ``recipe`` must come from get_recipes, create_recipe or update_recipe, with
        changes made by assigning attributes. The whole object goes back by POST to
        the collection, as the web client edits (its PUT branch keys off a field
        that is never set; live, PUT /food/recipe/{id} updates in place too). Unlike
        the web client, cookTimeText, servingSize and nutrition are re-sent as read
        rather than nulled. Cozi does not refuse a stale ``version``: the last write wins.

        Raises WriteVerificationError if Cozi stored a new recipe instead of
        updating; that duplicate is deleted first.
        """
        box_id = _saveable(recipe)
        body = {**recipe.to_wire(), "title": recipe.name}
        stored = parse_model(CoziRecipe, await self._request("POST", RECIPES, json=body))
        if stored.recipe_box_id != box_id:
            await self._discard_duplicate(stored.recipe_box_id, box_id)
        return stored

    async def _discard_duplicate(self, new_id: Optional[str], box_id: str) -> NoReturn:
        cleanup = "no new id came back to remove"
        if isinstance(new_id, str) and ID_PATTERN.fullmatch(new_id):
            try:
                await self._request("DELETE", f"/2103/{{acct}}/food/recipe/{new_id}")
                cleanup = f"the duplicate {new_id} was removed"
            except CoziException as e:
                logger.warning("Could not remove duplicate recipe %s: %s", new_id, type(e).__name__)
                cleanup = f"the duplicate {new_id} could NOT be removed"
        raise WriteVerificationError(
            f"Cozi stored recipe {box_id} as a new recipe instead of updating it; {cleanup}"
        )

    async def delete_recipe(self, recipe_box_id: str) -> None:
        """Delete a recipe from the box by its recipe_box_id."""
        recipe_box_id = validate_id(recipe_box_id, "recipe_box_id")
        await self._request("DELETE", f"/2103/{{acct}}/food/recipe/{recipe_box_id}")

    async def import_recipe(self, url: str, save: bool = False) -> CoziRecipe:
        """
        Scrape a recipe from a web page.

        With ``save=False`` (the web's Recipes page) the answer is an unsaved
        preview without a recipe_box_id; with ``save=True`` (its Calendar page) Cozi
        stores the recipe and answers with it. A page Cozi cannot fetch is a 400
        APIError (the reason is in ``response_data["detail"]``); a page with no
        recipe comes back as a recipe with no ingredients.
        """
        if not isinstance(url, str) or not url.strip():
            raise CoziValidationError("Recipe url cannot be empty")
        body = await self._request("POST", SCRAPE, json={"url": url, "save": bool(save)})
        if not isinstance(body, dict) or body.get("error"):
            raise APIError(
                f"Cozi could not import the recipe{_reason(body)}",
                response_data=body if isinstance(body, dict) else {"response": body},
            )
        return parse_model(CoziRecipe, body)

    async def get_curated_recipes(self) -> List[CoziRecipe]:
        """Cozi's curated recipe packs. Their recipes have no recipe_box_id."""
        return parse_models(CoziRecipe, await self._request("GET", CURATED))

    async def save_curated_recipe(self, recipe: CoziRecipe) -> CoziRecipe:
        """Copy a curated recipe into the box ("Add to my recipes") and return the copy."""
        if recipe.recipe_box_id is not None:
            # POST with a recipeBoxId updates that recipe in place; it would not copy.
            raise CoziValidationError("This recipe is already in the recipe box")
        body = {k: v for k, v in recipe.to_wire().items() if v is not None}
        return parse_model(CoziRecipe, await self._request("POST", RECIPES, json=body))

    async def upload_recipe_photo(self, recipe: CoziRecipe, data: bytes, filename: str) -> CoziRecipe:
        """
        Upload a photo and make it the recipe's first photo; returns the re-saved recipe.

        Like the web client, the upload is multipart field ``file`` with the token
        in the ``auth`` query, followed by a save of the recipe with the photo
        prepended: the upload alone does not attach it. ``recipe`` must be one
        update_recipe accepts.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise CoziValidationError("Photo data must be bytes")
        if len(data) > MAX_PHOTO_BYTES:
            raise CoziValidationError(f"Photo is {len(data)} bytes; Cozi accepts at most {MAX_PHOTO_BYTES}")
        if not isinstance(filename, str) or not filename.strip():
            raise CoziValidationError("Photo filename cannot be empty")
        # The re-save's checks, before the upload: a refused re-save would orphan the photo.
        box_id = _saveable(recipe)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        payload = bytes(data)

        def form() -> aiohttp.FormData:
            fd = aiohttp.FormData()
            fd.add_field("file", payload, filename=filename, content_type=content_type)
            return fd

        photo = await self._request(
            "POST", f"/2103/{{acct}}/food/recipe/{box_id}/photo", form=form, query_token=True
        )
        if not isinstance(photo, dict):
            # The web client swallows this and saves photos:[undefined, ...].
            raise ResponseParseError(
                f"Expected a photo object from Cozi, got {type(photo).__name__}",
                response_data={"response": photo},
            )
        return await self.update_recipe(recipe.model_copy(update={"photos": [photo, *(recipe.photos or [])]}))

    async def add_recipe_to_list(self, recipe: CoziRecipe, list_id: str) -> List["CoziItem"]:
        """
        Add a recipe to a list, as the web client does: a header with the recipe's
        name at the top, then one item per ingredient, in order.

        The items are added one request at a time and not retried, so a failure
        part-way leaves the items already added.
        """
        list_id = validate_id(list_id, "list_id")
        if not isinstance(recipe.name, str) or not recipe.name.strip():
            raise CoziValidationError("Recipe name cannot be empty")
        names = [i.name for i in recipe.ingredients or [] if isinstance(i.name, str) and i.name.strip()]
        items = [await self.add_item(list_id, recipe.name.upper(), position=0, header=True)]  # type: ignore[attr-defined]
        for position, name in enumerate(names, start=1):
            items.append(await self.add_item(list_id, name, position=position))  # type: ignore[attr-defined]
        return items
