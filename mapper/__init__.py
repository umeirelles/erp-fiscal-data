"""Mapeador de planilhas fiscais: um módulo por etapa, na ordem do fluxo.

spreadsheet → schema → headers → evidence → resolver → decide → report → apply;
catalogs fornece as tabelas oficiais, evaluate mede os relatórios com os gabaritos
e cli traduz o argparse. O entry point é ``run.py``, na raiz do repositório.
"""
