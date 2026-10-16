"""Recipes endpoints with aioresponses: exact method, path and body for each."""

import re
from unittest.mock import AsyncMock, call

import aiohttp
import pytest

from cozi_client.exceptions import (
    APIError,
    CoziValidationError,
    ResourceNotFoundError,
    ResponseParseError,
    WriteVerificationError,
)
from cozi_client.recipes import MAX_PHOTO_BYTES, CoziIngredient, CoziRecipe
from tests.conftest import TEST_ACCESS_TOKEN, api, sent, sent_query

RECIPES = "/2103/{acct}/food/recipe/"
SCRAPE = "/2103/{acct}/food/scrape/recipe/"
CURATED = "/2103/{acct}/food/curated/recipe/"
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

STORED = {
    "recipeBoxId": "rb1",
    "recipeId": "r1",
    "name": "Soup",
    "ingredients": [{"ingredientId": "i1", "name": "water"}, {"ingredientId": "i2", "name": "salt"}],
    "instructions": "boil",
    "tags": {"has-folder": ["Dinner"], "has-cuisine": ["French"]},
    "photos": [{"thumbUrl": "t0", "viewUrl": "v0"}],
    "creationDate": "2026-01-01T00:00:00",
    "cookTimeText": "30 min",
    "futureKey": 1,
}


def stored():
    return CoziRecipe.model_validate(STORED)


def bodies(mocked, method, path):
    return [c.get("json") for c in sent(mocked, method, path)]


async def test_get_recipes(client, mocked):
    mocked.get(api(RECIPES), payload=[STORED])
    recipes = await client.get_recipes()
    assert len(recipes) == 1
    assert recipes[0].recipe_box_id == "rb1"
    assert recipes[0].ingredients[1].name == "salt"
    assert recipes[0].categories == ["Dinner"]
    assert len(sent(mocked, "GET", RECIPES)) == 1


async def test_get_recipes_rejects_non_array(client, mocked):
    mocked.get(api(RECIPES), payload={"error": "x"})
    with pytest.raises(ResponseParseError):
        await client.get_recipes()


async def test_create_recipe_body(client, mocked):
    mocked.post(api(RECIPES), payload={**STORED, "recipeBoxId": "rb9"})
    recipe = await client.create_recipe(
        "Soup",
        ["2 cups water", "  ", "", "salt "],
        instructions="boil\nserve",
        description="d",
        notes="n",
        source="https://example.com/r",
        categories=["Dinner"],
    )
    assert recipe.recipe_box_id == "rb9"
    (body,) = bodies(mocked, "POST", RECIPES)
    ids = [i.pop("ingredientId") for i in body["ingredients"]]
    assert all(UUID4.fullmatch(i) for i in ids) and len(set(ids)) == 2
    assert body == {
        "recipeSource": "Typed Into Cozi",
        "cookTimeText": None,
        "servingSize": None,
        "nutrition": None,
        "title": "Soup",
        "name": "Soup",
        "sourceRaw": "https://example.com/r",
        "description": "d",
        "notes": "n",
        "isPublished": False,
        "tags": {"has-folder": ["Dinner"]},
        "ingredients": [{"name": "2 cups water"}, {"name": "salt "}],
        "instructions": "boil\nserve",
    }


async def test_create_recipe_splits_a_text_block_and_defaults(client, mocked):
    mocked.post(api(RECIPES), payload=STORED)
    await client.create_recipe("Soup", "water\r\n\nsalt\n")
    (body,) = bodies(mocked, "POST", RECIPES)
    assert [i["name"] for i in body["ingredients"]] == ["water", "salt"]
    assert (body["tags"], body["instructions"], body["sourceRaw"]) == ({}, "", "")


@pytest.mark.parametrize(
    "args",
    [
        ("  ", ["x"]),
        (None, ["x"]),
        ("Soup", ["x", 3]),
        ("Soup", 3),
        ("Soup", ["x"], None),
    ],
)
async def test_create_recipe_validates_before_io(client, mocked, args):
    with pytest.raises(CoziValidationError):
        await client.create_recipe(*args)
    assert sent(mocked, "POST", RECIPES) == []


async def test_create_recipe_rejects_non_str_category(client, mocked):
    with pytest.raises(CoziValidationError):
        await client.create_recipe("Soup", ["x"], categories=["ok", 1])
    assert sent(mocked, "POST", RECIPES) == []


async def test_update_recipe_round_trips_the_stored_object(client, mocked):
    mocked.post(api(RECIPES), payload=STORED)
    # The existence check reads the box raw: an unrelated recipe that would not parse is fine.
    mocked.get(api(RECIPES), payload=[{"recipeBoxId": "other", "ingredients": "not a list"}, STORED])
    recipe = stored()
    recipe.name = "Stew"
    recipe.ingredients = [*recipe.ingredients, CoziIngredient(ingredient_id="i3", name="pepper")]
    result = await client.update_recipe(recipe)
    assert result.recipe_box_id == "rb1"
    (body,) = bodies(mocked, "POST", RECIPES)
    assert body == {
        **STORED,
        "name": "Stew",
        "title": "Stew",
        "ingredients": [*STORED["ingredients"], {"ingredientId": "i3", "name": "pepper"}],
    }
    assert len(sent(mocked, "GET", RECIPES)) == 1


@pytest.mark.parametrize(
    "box,error",
    [([{"recipeBoxId": "other"}, "junk"], ResourceNotFoundError), ({"error": "x"}, ResponseParseError)],
)
async def test_update_of_a_deleted_recipe_raises(client, mocked, box, error):
    # Live: a POST to a deleted recipeBoxId answers 200 with that id and stores nothing.
    mocked.post(api(RECIPES), payload=STORED)
    mocked.get(api(RECIPES), payload=box)
    with pytest.raises(error, match="does not exist|Expected a list"):
        await client.update_recipe(stored())


async def test_update_recipe_rejects_hand_built_and_bad_ids_before_io(client, mocked):
    for recipe in (
        CoziRecipe(recipe_box_id="rb1", name="x"),
        CoziRecipe.model_validate({**STORED, "recipeBoxId": None}),
        CoziRecipe.model_validate({**STORED, "recipeBoxId": "../../evil"}),
        CoziRecipe.model_validate({**STORED, "name": " "}),
    ):
        with pytest.raises(CoziValidationError):
            await client.update_recipe(recipe)
    assert sent(mocked, "POST", RECIPES) == []


async def test_update_that_creates_a_duplicate_deletes_it_and_raises(client, mocked):
    mocked.post(api(RECIPES), payload={**STORED, "recipeBoxId": "rb2"})
    mocked.delete(api("/2103/{acct}/food/recipe/rb2"), status=204)
    with pytest.raises(WriteVerificationError, match="the duplicate rb2 was removed"):
        await client.update_recipe(stored())
    assert len(sent(mocked, "DELETE", "/2103/{acct}/food/recipe/rb2")) == 1


async def test_duplicate_cleanup_failure_is_reported(client, mocked, no_sleep):
    mocked.post(api(RECIPES), payload={**STORED, "recipeBoxId": "rb2"})
    mocked.delete(api("/2103/{acct}/food/recipe/rb2"), status=500, repeat=True)
    with pytest.raises(WriteVerificationError, match="could NOT be removed"):
        await client.update_recipe(stored())


async def test_update_answer_without_an_id_raises(client, mocked):
    mocked.post(api(RECIPES), payload={"name": "Soup"})
    with pytest.raises(WriteVerificationError, match="no new id came back"):
        await client.update_recipe(stored())


async def test_delete_recipe(client, mocked):
    mocked.delete(api("/2103/{acct}/food/recipe/rb1"), status=204)
    assert await client.delete_recipe("rb1") is None
    assert len(sent(mocked, "DELETE", "/2103/{acct}/food/recipe/rb1")) == 1
    with pytest.raises(CoziValidationError):
        await client.delete_recipe("../../evil")
    assert len(mocked.requests) == 2  # login + the one delete


async def test_import_recipe_body_and_minted_ids(client, mocked):
    mocked.post(api(SCRAPE), payload={"name": "Pancakes", "ingredients": [{"name": "flour"}, {"name": "milk"}]})
    recipe = await client.import_recipe("https://example.com/p")
    assert bodies(mocked, "POST", SCRAPE) == [{"url": "https://example.com/p", "save": False}]
    assert all(UUID4.fullmatch(i.ingredient_id) for i in recipe.ingredients)
    assert recipe.recipe_box_id is None


async def test_import_recipe_save_true_is_passed_through(client, mocked):
    mocked.post(api(SCRAPE), payload={"name": "Pancakes", "recipeBoxId": "rb7"})
    await client.import_recipe("https://example.com/p", save=True)
    assert bodies(mocked, "POST", SCRAPE) == [{"url": "https://example.com/p", "save": True}]


@pytest.mark.parametrize("status,payload", [(200, {"error": "unparseable"}), (400, {"error": "unparseable"}), (200, [])])
async def test_import_recipe_error_body_raises(client, mocked, status, payload):
    mocked.post(api(SCRAPE), status=status, payload=payload)
    with pytest.raises(APIError) as e:
        await client.import_recipe("https://example.com/p")
    assert type(e.value) is APIError


async def test_import_recipe_rejects_blank_url(client, mocked):
    with pytest.raises(CoziValidationError):
        await client.import_recipe(" ")
    assert sent(mocked, "POST", SCRAPE) == []


async def test_get_curated_recipes(client, mocked):
    mocked.get(api(CURATED), payload=[{"recipeId": "c1", "name": "Pie", "tags": {"in-curated-pack": ["Fall"]}}])
    (pie,) = await client.get_curated_recipes()
    assert (pie.recipe_id, pie.recipe_box_id, pie.tags) == ("c1", None, {"in-curated-pack": ["Fall"]})
    assert len(sent(mocked, "GET", CURATED)) == 1


async def test_save_curated_recipe_strips_nulls_and_keeps_tags(client, mocked):
    curated = CoziRecipe.model_validate(
        {
            "recipeId": "c1",
            "name": "Pie",
            "tags": {"in-curated-pack": ["Fall"]},
            "cookTimeText": None,
            "sourceLogoUrl": None,
            "ingredients": [{"ingredientId": "i1", "name": "apple", "unit": None}],
        }
    )
    mocked.post(api(RECIPES), payload={"recipeBoxId": "rb5", "recipeId": "c1", "name": "Pie"})
    saved = await client.save_curated_recipe(curated)
    assert saved.recipe_box_id == "rb5"
    assert bodies(mocked, "POST", RECIPES) == [
        {
            "recipeId": "c1",
            "name": "Pie",
            "tags": {"in-curated-pack": ["Fall"]},
            "ingredients": [{"ingredientId": "i1", "name": "apple", "unit": None}],
        }
    ]


async def test_save_curated_recipe_refuses_a_box_recipe(client, mocked):
    # A POST carrying a recipeBoxId updates that recipe in place instead of copying.
    with pytest.raises(CoziValidationError):
        await client.save_curated_recipe(stored())
    assert sent(mocked, "POST", RECIPES) == []


PHOTO = "/2103/{acct}/food/recipe/rb1/photo"


async def test_upload_recipe_photo_then_resaves_with_photo_first(client, mocked):
    mocked.get(api(RECIPES), payload=[STORED], repeat=True)
    mocked.post(api(PHOTO), payload={"thumbUrl": "t1", "viewUrl": "v1"})
    mocked.post(api(RECIPES), payload=STORED)
    await client.upload_recipe_photo(stored(), b"\x89PNG", "pixel.png")
    (upload,) = sent(mocked, "POST", PHOTO)
    assert isinstance(upload["data"], aiohttp.FormData)
    assert "Authorization" not in upload["headers"]
    query = sent_query(mocked, "POST", PHOTO)[0]
    assert query["auth"] == TEST_ACCESS_TOKEN
    assert query["apikey"] == client.APIKEY
    (body,) = bodies(mocked, "POST", RECIPES)
    assert body["photos"] == [{"thumbUrl": "t1", "viewUrl": "v1"}, *STORED["photos"]]
    assert body == {**STORED, "title": "Soup", "photos": body["photos"]}
    assert len(sent(mocked, "GET", RECIPES)) == 2  # before the upload, and after the re-save


async def test_upload_to_a_deleted_recipe_raises_before_uploading(client, mocked):
    mocked.get(api(RECIPES), payload=[])
    with pytest.raises(ResourceNotFoundError, match="does not exist"):
        await client.upload_recipe_photo(stored(), b"x", "a.png")
    assert sent(mocked, "POST", PHOTO) == [] and sent(mocked, "POST", RECIPES) == []


async def test_photo_size_cap_is_inclusive_and_checked_before_io(client, mocked):
    with pytest.raises(CoziValidationError, match="at most"):
        await client.upload_recipe_photo(stored(), b"\0" * (MAX_PHOTO_BYTES + 1), "big.png")
    assert sent(mocked, "POST", PHOTO) == []
    mocked.get(api(RECIPES), payload=[STORED], repeat=True)
    mocked.post(api(PHOTO), payload={"thumbUrl": "t1"})
    mocked.post(api(RECIPES), payload=STORED)
    await client.upload_recipe_photo(stored(), b"\0" * MAX_PHOTO_BYTES, "max.png")
    assert len(sent(mocked, "POST", PHOTO)) == 1


@pytest.mark.parametrize("data,filename,box", [(None, "a.png", "rb1"), ("str", "a.png", "rb1"), (b"x", " ", "rb1"), (b"x", "a.png", None)])
async def test_upload_recipe_photo_validates_before_io(client, mocked, data, filename, box):
    recipe = CoziRecipe.model_validate({**STORED, "recipeBoxId": box})
    with pytest.raises(CoziValidationError):
        await client.upload_recipe_photo(recipe, data, filename)
    assert len(mocked.requests) == 1  # the login only


@pytest.mark.parametrize(
    "recipe",
    [CoziRecipe(recipe_box_id="rb1", name="Soup"), CoziRecipe.model_validate({**STORED, "name": " "})],
)
async def test_upload_refuses_a_recipe_the_resave_would_refuse_before_uploading(client, mocked, recipe):
    with pytest.raises(CoziValidationError):
        await client.upload_recipe_photo(recipe, b"x", "a.png")
    assert len(mocked.requests) == 1  # the login only: no orphaned upload


async def test_upload_non_object_answer_raises_and_does_not_resave(client, mocked):
    mocked.get(api(RECIPES), payload=[STORED])
    mocked.post(api(PHOTO), status=204)
    with pytest.raises(ResponseParseError):
        await client.upload_recipe_photo(stored(), b"x", "a.png")
    assert sent(mocked, "POST", RECIPES) == []


async def test_add_recipe_to_list_header_then_ingredients(client, mocked):
    client.add_item = AsyncMock(side_effect=lambda *a, **k: (a, k))
    recipe = CoziRecipe.model_validate(
        {**STORED, "ingredients": [{"name": "water"}, {"name": " "}, {"name": None}, {"name": "salt"}]}
    )
    items = await client.add_recipe_to_list(recipe, "l1")
    assert client.add_item.await_args_list == [
        call("l1", "SOUP", position=0, header=True),
        call("l1", "water", position=1),
        call("l1", "salt", position=2),
    ]
    assert len(items) == 3
    assert len(mocked.requests) == 1  # nothing but the login went over the wire


async def test_add_recipe_to_list_validates_before_any_add(client):
    client.add_item = AsyncMock()
    with pytest.raises(CoziValidationError):
        await client.add_recipe_to_list(stored(), "../evil")
    with pytest.raises(CoziValidationError):
        await client.add_recipe_to_list(CoziRecipe.model_validate({**STORED, "name": ""}), "l1")
    assert client.add_item.await_count == 0


async def test_add_recipe_to_list_refuses_a_non_ingredient_entry(client, mocked):
    # In-place .append is not seen by validate_assignment.
    recipe = stored()
    recipe.ingredients.append("pepper")
    with pytest.raises(CoziValidationError, match="CoziIngredient"):
        await client.add_recipe_to_list(recipe, "l1")
    assert len(mocked.requests) == 1  # the login only


ITEMS = "/2004/{acct}/list/l1/item/"


async def test_add_recipe_to_list_refuses_a_header_like_line_before_any_add(client, mocked):
    # The real add_item: it would refuse "A" (Cozi stores it as a header) only after
    # the header and "water" were already on the list.
    mocked.post(api(ITEMS), payload={"itemId": "h", "text": "SOUP", "itemType": "header"})
    mocked.post(api(ITEMS), payload={"itemId": "w", "text": "water"})
    recipe = CoziRecipe.model_validate({**STORED, "ingredients": [{"name": "water"}, {"name": "A"}]})
    with pytest.raises(CoziValidationError, match="section header"):
        await client.add_recipe_to_list(recipe, "l1")
    assert sent(mocked, "POST", ITEMS) == []
