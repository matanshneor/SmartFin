/*
 * No transaction without a category: Matan's decision (30.9).
 *
 * A household transaction must have a `category_id`, and a project transaction
 * a `project_category_id`. The server already rejects a save without a
 * category, and deleting a category moves its transactions before it is
 * deleted. The constraint is the net underneath: both FKs are `ON DELETE SET
 * NULL`, so a transaction added to the category between the move and the
 * delete (or any future route that forgets to move) fails the delete instead
 * of leaving an orphan transaction.
 *
 * The ten old transactions without a category were assigned before the
 * migration, per Matan's choice.
 */
alter table public.transactions
    add constraint transactions_category_required check (
        case when project_id is null then category_id is not null
             else project_category_id is not null
        end
    );
