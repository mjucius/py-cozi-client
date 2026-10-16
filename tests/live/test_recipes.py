"""
Live: the recipe box against the real Cozi API.

Everything created is named subject("recipes") so the sweeper finds it, except the
copies of curated recipes and imports, which keep their own names: those tests
snapshot the box, delete only the ids they were handed back (or new ids with the
imported name), and end by asserting the box matches the snapshot.

Observed 2026-10-16 (and pinned below): recipes are not gated on this account;
an empty box is []; recipeId differs from recipeBoxId; `version` is a counter that
every write bumps, and a stale one is not refused; PUT /food/recipe/{id} also
updates in place; a second DELETE is 404; an upload does not attach the photo by
itself, and a Bearer-only upload is accepted too.
"""

import inspect

import aiohttp
import pytest

from cozi_client import ID_PATTERN
from cozi_client.exceptions import APIError, CoziException, ResourceNotFoundError
from cozi_client.recipes import RECIPES, CoziIngredient, CoziRecipe
from tests.live.conftest import domain_sweeper, subject

_sweep = domain_sweeper("recipes")

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c6360000200000500017a5eab3f0000000049454e44ae426082"
)
# Scraping is done by Cozi's server and depends on the page: allrecipes.com and
# simplyrecipes.com answered 400 "Could not retrieve recipe." on 2026-10-16.
IMPORT_URL = "https://www.bbcgoodfood.com/recipes/easy-pancakes"


async def box(cozi):
    return {r.recipe_box_id: r for r in await cozi.get_recipes()}


async def make(cozi, **kwargs):
    kwargs.setdefault("instructions", "boil\nserve")
    return await cozi.create_recipe(subject("recipes"), ["2 cups water", "", "1 tsp salt"], **kwargs)


async def test_create_read_update_delete(live_client):
    cozi = live_client
    created = await make(
        cozi,
        description="desc",
        notes="note",
        source="https://example.com/r",
        categories=["LIVE_TEST_DELETE_ME"],
    )
    box_id = created.recipe_box_id
    assert ID_PATTERN.fullmatch(box_id) and ID_PATTERN.fullmatch(created.recipe_id)
    assert created.recipe_id != box_id
    # Cozi does not store or echo recipeSource or title for a typed recipe.
    assert (created.recipe_source, created.model_extra.get("title")) == (None, None)

    read = (await box(cozi))[box_id]
    assert read.to_wire() == created.to_wire()
    assert read.name == created.name
    assert [i.name for i in read.ingredients] == ["2 cups water", "1 tsp salt"]
    assert [i.ingredient_id for i in read.ingredients] == [i.ingredient_id for i in created.ingredients]
    assert (read.instructions, read.description, read.notes, read.source_raw) == (
        "boil\nserve",
        "desc",
        "note",
        "https://example.com/r",
    )
    assert read.tags == {"has-folder": ["LIVE_TEST_DELETE_ME"]}
    assert read.categories == ["LIVE_TEST_DELETE_ME"]

    count = len(await box(cozi))
    read.name = subject("recipes")
    read.ingredients = [*read.ingredients, CoziIngredient(name="pepper")]
    updated = await cozi.update_recipe(read)
    assert (updated.recipe_box_id, updated.recipe_id) == (box_id, read.recipe_id)
    assert updated.model_extra["version"] > read.model_extra["version"]
    after = await box(cozi)
    assert len(after) == count
    assert after[box_id].name == read.name
    assert [i.name for i in after[box_id].ingredients] == ["2 cups water", "1 tsp salt", "pepper"]
    assert after[box_id].recipe_id == read.recipe_id

    await cozi.delete_recipe(box_id)
    assert box_id not in await box(cozi)
    with pytest.raises(ResourceNotFoundError):
        await cozi.delete_recipe(box_id)


async def test_put_updates_in_place_and_stale_version_is_not_refused(live_client):
    """The web client never PUTs (its PUT branch is dead); this pins what a PUT does."""
    cozi = live_client
    recipe = await make(cozi)
    box_id = recipe.recipe_box_id
    try:
        before = set(await box(cozi))
        new_name = subject("recipes")
        status, body = await cozi._request_with_status(
            "PUT", f"/2103/{{acct}}/food/recipe/{box_id}", json={**recipe.to_wire(), "name": new_name, "title": new_name}
        )
        assert (status, body["recipeBoxId"], body["name"]) == (200, box_id, new_name)
        assert body["version"] > recipe.model_extra["version"]
        after = await box(cozi)
        assert set(after) == before and after[box_id].name == new_name

        # `recipe` still carries the pre-PUT version: last write wins, no 409.
        recipe.name = subject("recipes")
        stored = await cozi.update_recipe(recipe)
        assert stored.name == recipe.name
        after = await box(cozi)
        assert set(after) == before and after[box_id].name == recipe.name
    finally:
        await cozi.delete_recipe(box_id)


async def test_photo_upload_and_resave(live_client):
    cozi = live_client
    recipe = await make(cozi)
    try:
        saved = await cozi.upload_recipe_photo(recipe, PNG, "pixel.png")
        assert saved.recipe_box_id == recipe.recipe_box_id
        (photo,) = saved.photos
        # The re-save adds `text: null` to the four keys the upload answered with.
        assert set(photo) == {"originalUrl", "photoId", "text", "thumbUrl", "viewUrl"} and photo["text"] is None
        assert all(isinstance(photo[k], str) for k in ("originalUrl", "photoId", "thumbUrl", "viewUrl"))
        assert (await box(cozi))[recipe.recipe_box_id].photos == saved.photos

        # A Bearer-only upload (no ?auth=) is accepted too, and an upload on its own
        # does not attach the photo: the re-save is what does.
        def form():
            fd = aiohttp.FormData()
            fd.add_field("file", PNG, filename="pixel.png", content_type="image/png")
            return fd

        status, body = await cozi._request_with_status(
            "POST", f"/2103/{{acct}}/food/recipe/{recipe.recipe_box_id}/photo", form=form
        )
        assert status == 200 and set(body) == {"originalUrl", "photoId", "thumbUrl", "viewUrl"}
        assert (await box(cozi))[recipe.recipe_box_id].photos == saved.photos
    finally:
        await cozi.delete_recipe(recipe.recipe_box_id)


async def test_curated_save_and_delete(live_client):
    cozi = live_client
    snapshot = set(await box(cozi))
    taken = {r.recipe_id for r in (await box(cozi)).values()} | snapshot
    curated = await cozi.get_curated_recipes()
    assert curated
    assert all(r.recipe_box_id is None and r.recipe_id and (r.tags or {}).get("in-curated-pack") for r in curated)
    eligible = [r for r in curated if r.recipe_id not in taken]
    if len(eligible) < 2:
        pytest.skip("fewer than two curated recipes are not already in the box")
    a, b = eligible[:2]
    saved = []
    try:
        copy_a = await cozi.save_curated_recipe(a)  # tags as read (the plan's shape)
        saved.append(copy_a.recipe_box_id)
        # The web's shape (verifier: VPjR.G nulls tags, m() strips nulls): no tags key.
        raw = {k: v for k, v in b.to_wire().items() if v is not None and k != "tags"}
        copy_b = CoziRecipe.model_validate(await cozi._request("POST", RECIPES, json=raw))
        saved.append(copy_b.recipe_box_id)
        stored = await box(cozi)
        for original, copy in ((a, copy_a), (b, copy_b)):
            assert ID_PATTERN.fullmatch(copy.recipe_box_id) and copy.recipe_box_id not in snapshot
            got = stored[copy.recipe_box_id]
            assert (got.recipe_id, got.name) == (original.recipe_id, original.name)
            # Cozi derives the copy's tags from the curated recipeId either way.
            assert got.tags == original.tags
    finally:
        # Keyed on the curated recipeIds too, so a save that stored a copy and then
        # raised still has its copy removed (the name carries no sweepable prefix).
        after = await box(cozi)
        for rid in set(after) - snapshot:
            if rid in saved or after[rid].recipe_id in (a.recipe_id, b.recipe_id):
                await cozi.delete_recipe(rid)
    assert set(await box(cozi)) == snapshot


@pytest.mark.parametrize("save", [False, True])
async def test_import_recipe(live_client, save):
    cozi = live_client
    snapshot = set(await box(cozi))
    recipe = None
    try:
        try:
            recipe = await cozi.import_recipe(IMPORT_URL, save=save)
        except CoziException as e:
            pytest.skip(f"import of a third-party page failed: {type(e).__name__} {e.status_code}")
        assert recipe.name and recipe.ingredients
        assert all(ID_PATTERN.fullmatch(i.ingredient_id) for i in recipe.ingredients)
        assert recipe.source_raw == IMPORT_URL
        after = await box(cozi)
        new = set(after) - snapshot
        if save:
            # save=true stores the recipe and answers with the stored object, whose
            # ingredient ids Cozi minted (they match the stored ones).
            assert ID_PATTERN.fullmatch(recipe.recipe_box_id) and new == {recipe.recipe_box_id}
            assert recipe.model_extra["sourceUrl"] == IMPORT_URL
            stored = after[recipe.recipe_box_id]
            assert [i.ingredient_id for i in stored.ingredients] == [i.ingredient_id for i in recipe.ingredients]
        else:
            # save=false is a preview: no id, no sourceUrl, nothing stored; its
            # ingredients arrive with only a name, so the ids are minted here.
            assert recipe.recipe_box_id is None and "sourceUrl" not in recipe.model_extra and new == set()
    finally:
        # Keyed on the source url, not the answer: a save=true import that stored the
        # recipe and then raised must still be removed (its name has no prefix).
        after = await box(cozi)
        for rid in set(after) - snapshot:
            r = after[rid]
            if (recipe is not None and rid == recipe.recipe_box_id) or IMPORT_URL in (
                r.source_raw,
                r.model_extra.get("sourceUrl"),
            ):
                await cozi.delete_recipe(rid)
    assert set(await box(cozi)) == snapshot


async def test_import_failure_is_a_400_not_a_200_error_body(live_client):
    # (A page without a recipe, e.g. example.com, is a 200 with a name and no ingredients.)
    snapshot = set(await box(live_client))
    with pytest.raises(APIError) as e:
        await live_client.import_recipe("https://no-such-host.invalid/x")
    assert e.value.status_code == 400
    assert e.value.response_data["detail"] == "Could not resolve hostname: no-such-host.invalid"
    assert set(await box(live_client)) == snapshot


async def test_add_recipe_to_list(live_client):
    cozi = live_client
    add_item = getattr(cozi, "add_item", None)
    if add_item is None or "header" not in inspect.signature(add_item).parameters:
        pytest.skip("ListsMixin.add_item(list_id, text, position=, header=) has not landed yet")
    lst = await cozi._request("POST", "/2004/{acct}/list/", json={"listType": "shopping", "title": subject("recipes")})
    list_id = lst["listId"]
    recipe = await make(cozi)
    try:
        added = await cozi.add_recipe_to_list(recipe, list_id)
        assert len(added) == 3
        (stored,) = [x for x in await cozi._request("GET", "/2004/{acct}/list/") if x["listId"] == list_id]
        assert [(i["text"], i["itemType"]) for i in stored["items"]] == [
            (recipe.name.upper(), "header"),
            ("2 cups water", None),
            ("1 tsp salt", None),
        ]
    finally:
        await cozi._request("DELETE", f"/2004/{{acct}}/list/{list_id}")
        await cozi.delete_recipe(recipe.recipe_box_id)
