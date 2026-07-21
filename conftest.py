"""Sorgt dafür, dass PyTest das Projekt-Root im Importpfad hat."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
