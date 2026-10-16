USE controle_despesas;
SELECT VERSION() AS versao, DATABASE() AS banco_atual;
SELECT TABLE_NAME AS nome, TABLE_TYPE AS tipo
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'controle_despesas'
ORDER BY TABLE_TYPE, TABLE_NAME;
SELECT
 (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='controle_despesas' AND TABLE_TYPE='BASE TABLE') AS tabelas,
 (SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='controle_despesas' AND TABLE_TYPE='VIEW') AS views,
 (SELECT COUNT(*) FROM categorias) AS categorias,
 (SELECT COUNT(*) FROM formas_pagamento) AS formas_pagamento,
 (SELECT COUNT(*) FROM despesas) AS despesas;
SELECT * FROM vw_despesas_detalhadas;
SELECT * FROM vw_gastos_diarios;
SELECT * FROM vw_gastos_mensais;
SELECT * FROM vw_gastos_por_categoria;
