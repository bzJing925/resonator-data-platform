import numpy as np
import pandas as pd

from app.ml.optimization_ablation import RECIPES
from app.ml.optimization_boundary_selection import (
    balanced_scores,
    pseudo_boundaries,
    stress_selection,
)
from tests.ml.test_optimization_p6a import catalog


def test_edge_partner_is_embargoed_and_constant_axes_reported():
    frame, _ = catalog()
    m, capacity = pseudo_boundaries(frame)
    edge = m[m.scenario.eq("area_um2_low")]
    assert edge.loc[edge.structure_id.eq("s0_0"), "role"].item() == "evaluation"
    assert edge.loc[edge.structure_id.eq("s0_1"), "role"].item() == "embargo"
    assert not set(edge[edge.role.eq("train")].design_group_id) & set(
        edge[edge.role.ne("train")].design_group_id
    )
    assert capacity[capacity.axis.eq("eg_um")].skip_reason.eq("fewer_than_three_axis_levels").all()


def test_balanced_score_is_invariant_to_number_of_groups_in_large_branch():
    def sample(n):
        records = []
        fit = []
        for branch, count in [("small", 1), ("large", n)]:
            for i in range(count):
                sid = f"{branch}{i}"
                fit.append(dict(structure_id=sid, branch_id=branch))
                for recipe in RECIPES:
                    error = 1 if branch == "small" else 0.1
                    records.append(
                        dict(
                            structure_id=sid,
                            design_group_id=sid,
                            recipe=recipe,
                            target="qp",
                            abs_log_error=error,
                        )
                    )
        return balanced_scores(pd.DataFrame(records), pd.DataFrame(fit))[1]

    assert sample(2) == sample(100)


def test_stress_scores_cover_all_recipes_and_use_group_means():
    frame, _ = catalog()
    selected, scores, manifests, capacity, pred, cells = stress_selection(frame)
    assert selected in RECIPES and set(scores) == set(RECIPES)
    assert set(pred.structure_id) <= set(frame.structure_id)
    assert set(cells.n_design_groups) == {1}
    for recipe in RECIPES:
        expected = cells[cells.recipe.eq(recipe)].mean_abs_log_error.max()
        np.testing.assert_allclose(expected, scores[recipe]["worst_cell"])
