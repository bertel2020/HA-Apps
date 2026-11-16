"""Empfehlung der Ausreißer-Schwelle (Grundlage: Profil je Stufe aus dem Gesamt-Durchlauf).

Dieses Modul wächst schrittweise; bisher nur die Messgrundlage.
"""

from __future__ import annotations

# Zeitraum (Tage rückwärts), dessen Treffer je Stufe für die Empfehlung gezählt werden.
WINDOW_DAYS = 60
