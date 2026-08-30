# -*- coding: utf-8 -*-
"""把 yt-dlp 的英文报错翻译成用户能据此行动的说明。

平台本身不是问题：yt-dlp 内置约 1750 个提取器，TikTok、Instagram、X、Facebook、
Vimeo、Twitch、SoundCloud、B站等都在内，代码里也没有任何白名单。真正的问题是失败
时用户看到一句英文技术信息，分不清「要登录」「被地区限制」「视频已删除」和
「提取器过时」——而这四种情况的应对完全不同。

纯函数，不依赖 yt-dlp，可单独测试。
"""

import re
from typing import Dict, List, Tuple

# 顺序有意义：先匹配更具体的原因。例如「私享视频」也含 "unavailable" 字样。
_RULES: List[Tuple[str, str, str, str]] = [
    (
        r"sign in to confirm your age|age.?restricted|inappropriate for some users",
        "age_restricted",
        "Cette vidéo est réservée aux adultes et la plateforme exige une connexion.",
        "Connectez un navigateur dans Paramètres pour réutiliser votre session.",
    ),
    (
        r"private video|this video is private|this post is private|account is private",
        "private",
        "Ce contenu est privé.",
        "Seul un compte autorisé peut y accéder. Connectez un navigateur dans Paramètres si vous y avez droit.",
    ),
    (
        r"sign in|log in|login required|requires authentication|use --cookies"
        r"|cookies.*required|authentication|not a bot|confirm you.?re not a robot",
        "login_required",
        "La plateforme demande une connexion pour ce contenu.",
        "Dans Paramètres, indiquez un navigateur où vous êtes déjà connecté : l'application réutilisera cette session.",
    ),
    (
        # « has not made this video available in your country » : le motif doit
        # porter sur « available in your country », pas sur « not available… »
        r"available in your country|geo.?restrict|geo.?block|blocked in your country"
        r"|available from your location",
        "geo_blocked",
        "Ce contenu n'est pas accessible depuis votre pays.",
        "La plateforme le bloque selon la localisation ; l'application ne peut pas le contourner.",
    ),
    (
        r"http error 429|too many requests|rate.?limit",
        "rate_limited",
        "La plateforme a temporairement bloqué les téléchargements depuis votre connexion.",
        "Attendez quelques minutes avant de réessayer.",
    ),
    (
        # « not found » seul est trop large : « ffmpeg not found » n'a rien à voir
        # avec une vidéo supprimée. On s'en tient aux formulations sans ambiguïté.
        r"video unavailable|has been removed|no longer available|content isn.?t available"
        r"|deleted|http error 404",
        "unavailable",
        "Cette vidéo n'existe plus ou a été retirée.",
        "Vérifiez que le lien s'ouvre encore dans votre navigateur.",
    ),
    (
        r"unsupported url|no video formats found|no media found",
        "unsupported",
        "Ce lien ne contient pas de média exploitable.",
        "Donnez le lien direct de la vidéo plutôt que celui d'une page de profil ou de résultats.",
    ),
    (
        # Avant « extracteur périmé » : « Unable to download webpage: timed out »
        # est une panne réseau, pas un extracteur cassé.
        r"timed out|timeout|connection reset|network is unreachable|temporary failure in name resolution"
        r"|getaddrinfo|ssl|certificate",
        "network",
        "La connexion à la plateforme a échoué.",
        "Vérifiez votre connexion internet, puis réessayez.",
    ),
    (
        r"unable to extract|failed to extract|extractor.*(?:error|failed)"
        r"|player response|nsig extraction",
        "extractor_outdated",
        "La plateforme a changé son fonctionnement et le module de téléchargement ne suit plus.",
        "C'est un problème connu et temporaire : une mise à jour de l'application le corrigera.",
    ),
    (
        r"ffmpeg|ffprobe",
        "ffmpeg_missing",
        "L'outil de conversion audio est introuvable.",
        "Réinstallez l'application : ffmpeg est normalement fourni avec elle.",
    ),
]


def explain(error: object) -> Dict[str, str]:
    """返回 {reason, message, hint, detail}。

    未识别时 reason 为 "unknown"，并把原始信息放进 detail —— 宁可展示一句英文，
    也不能让用户什么都看不到。
    """
    detail = str(error or "").strip()
    haystack = detail.lower()

    for pattern, reason, message, hint in _RULES:
        if re.search(pattern, haystack):
            return {"reason": reason, "message": message, "hint": hint, "detail": detail}

    return {
        "reason": "unknown",
        "message": "Le téléchargement a échoué.",
        "hint": "Vérifiez que le lien s'ouvre dans votre navigateur, puis réessayez.",
        "detail": detail,
    }


def format_for_user(error: object) -> str:
    """一行式说明，用于任务状态栏。"""
    info = explain(error)
    return f"{info['message']} {info['hint']}".strip()
