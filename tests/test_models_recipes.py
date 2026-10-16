"""CoziRecipe / CoziIngredient, without any HTTP (also runs on aiohttp>=3.14)."""

import copy
import re

from cozi_client.recipes import CoziIngredient, CoziRecipe

UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

STORED = {
    "recipeBoxId": "rb1",
    "recipeId": "r1",
    "name": "Soup",
    "title": "Soup",
    "description": "hot",
    "ingredients": [
        {"ingredientId": "i1", "name": "water", "parsedName": None, "amount": "1", "unit": "cup"},
        {"ingredientId": "i2", "name": "salt"},
    ],
    "instructions": "boil\nserve",
    "notes": "",
    "sourceRaw": "https://example.com/r",
    "sourceUrl": "https://example.com/r",
    "tags": {"has-folder": ["Dinner"], "has-keyword": ["quick"], "has-cuisine": ["French"]},
    "photos": [{"thumbUrl": "t", "viewUrl": "v"}],
    "isPublished": False,
    "recipeSource": "Typed Into Cozi",
    "creationDate": "2026-01-01T00:00:00",
    "cookTimeText": "30 min",
    "servingSize": None,
    "futureKey": {"nested": [1]},
}


def test_stored_object_round_trips_exactly():
    raw = copy.deepcopy(STORED)
    recipe = CoziRecipe.model_validate(raw)
    assert recipe.to_wire() == STORED
    assert raw == STORED  # input not mutated
    assert recipe.ingredients[0].ingredient_id == "i1"
    assert recipe.recipe_box_id == "rb1" and recipe.source_raw == "https://example.com/r"


def test_assigned_and_copied_fields_are_sent():
    recipe = CoziRecipe.model_validate({"recipeBoxId": "rb1", "name": "Soup"})
    recipe.name = "Stew"
    recipe.notes = "x"
    copied = recipe.model_copy(update={"photos": [{"thumbUrl": "t"}]})
    assert copied.to_wire() == {
        "recipeBoxId": "rb1",
        "name": "Stew",
        "notes": "x",
        "photos": [{"thumbUrl": "t"}],
    }


def test_ingredient_ids_minted_when_missing_or_null_and_kept_otherwise():
    recipe = CoziRecipe.model_validate(
        {"ingredients": [{"name": "a"}, {"ingredientId": None, "name": "b"}, {"ingredientId": "keep", "name": "c"}]}
    )
    ids = [i.ingredient_id for i in recipe.ingredients]
    assert UUID4.fullmatch(ids[0]) and UUID4.fullmatch(ids[1]) and ids[0] != ids[1]
    assert ids[2] == "keep"
    assert recipe.to_wire()["ingredients"][0] == {"ingredientId": ids[0], "name": "a"}
    assert UUID4.fullmatch(CoziIngredient(name="x").ingredient_id)
    assert CoziIngredient(ingredient_id="given").ingredient_id == "given"


def test_categories_are_folder_plus_keyword_tags():
    assert CoziRecipe.model_validate(STORED).categories == ["Dinner", "quick"]
    assert CoziRecipe.model_validate({"tags": None}).categories == []
    assert CoziRecipe.model_validate({"tags": {"has-folder": None, "in-curated-pack": ["P"]}}).categories == []


def test_nulls_anywhere_do_not_fail_a_read():
    recipe = CoziRecipe.model_validate(
        {"name": None, "ingredients": None, "photos": None, "tags": None, "ingredientsX": None}
    )
    assert recipe.to_wire() == {"name": None, "ingredients": None, "photos": None, "tags": None, "ingredientsX": None}
