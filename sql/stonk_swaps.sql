-- Wszystkie swapy STONK (6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx)
-- w okresie 2026-09-06 00:00 UTC – 2026-09-26 00:00 UTC.
-- Jeden wiersz = jedna noga swapu, w której STONK jest kupowany lub sprzedawany.
SELECT
    t.block_time,
    t.block_slot,
    t.tx_id,
    t.outer_instruction_index,
    t.inner_instruction_index,
    t.trader_id,
    t.project,
    t.version,
    t.project_program_id,
    t.project_main_id,
    t.trade_source,
    t.token_bought_mint_address,
    t.token_bought_symbol,
    t.token_bought_amount,
    t.token_sold_mint_address,
    t.token_sold_symbol,
    t.token_sold_amount,
    t.amount_usd,
    t.token_bought_vault,
    t.token_sold_vault
FROM dex_solana.trades t
WHERE t.block_month IN (DATE '2026-09-01')
  AND t.block_time >= TIMESTAMP '2026-09-06 00:00:00'
  AND t.block_time <  TIMESTAMP '2026-09-26 00:00:00'
  AND (
        t.token_bought_mint_address = '6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx'
     OR t.token_sold_mint_address   = '6GmAFSYs4gk3FDao5FzzySQpPZaWsa4rUJHacpMpUNgx'
  )
ORDER BY t.block_time, t.tx_id, t.outer_instruction_index, t.inner_instruction_index
