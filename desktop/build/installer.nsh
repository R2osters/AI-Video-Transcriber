; Script NSIS personnalisé, inclus par electron-builder (nsis.include).
; Fichier en UTF-8 AVEC BOM : makensis en a besoin pour les accents.
;
; Règle produit : on ne supprime JAMAIS les données utilisateur en silence.
; %LOCALAPPDATA%\AI-Video-Transcriber contient la bibliothèque de transcriptions
; (textes, sous-titres et audios d'origine conservés, sans purge automatique) —
; elle peut peser plusieurs Go. Le désinstalleur demande, et garde par défaut.

!macro customUnInstall
  ${ifNot} ${isUpdated}
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
      "Supprimer aussi votre bibliothèque de transcriptions ?$\r$\n$\r$\n\
$LOCALAPPDATA\AI-Video-Transcriber$\r$\n$\r$\n\
Ce dossier contient vos transcriptions, vos sous-titres et les fichiers audio d'origine.$\r$\n\
Répondez Non pour les conserver (recommandé)." \
      /SD IDNO IDYES AvtDeleteUserData IDNO AvtKeepUserData
    AvtDeleteUserData:
      RMDir /r "$LOCALAPPDATA\AI-Video-Transcriber"
      Goto AvtUserDataDone
    AvtKeepUserData:
    AvtUserDataDone:
  ${endIf}
!macroend
