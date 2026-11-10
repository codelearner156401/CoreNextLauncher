"""Shared constants.

``DEFAULT_MS_CLIENT_ID`` is the public Microsoft OAuth client id published in
PrismLauncher's source (CMakeLists.txt, GPL-3.0). It can be replaced with your
own Azure application registration in launcher_config.json
(``microsoft_client_id``) without touching the code.
"""

APP_NAME = "CoreNextLauncher"
APP_VERSION = "1.8.1"

DEFAULT_MS_CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"

MS_DEVICE_CODE_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode"
MS_TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
MS_SCOPE = "XboxLive.SignIn XboxLive.offline_access"
XBL_AUTH_URL = "https://user.auth.xboxlive.com/user/authenticate"
XSTS_AUTH_URL = "https://xsts.auth.xboxlive.com/xsts/authorize"
MC_LOGIN_URL = "https://api.minecraftservices.com/authentication/login_with_xbox"
MC_PROFILE_URL = "https://api.minecraftservices.com/minecraft/profile"
MC_ENTITLEMENTS_URL = "https://api.minecraftservices.com/entitlements/mcstore"

# --- third-party services used by the 百宝箱 -------------------------------
ADOPTIUM_API = "https://api.adoptium.net/v3"
FABRIC_META = "https://meta.fabricmc.net/v2"
MODRINTH_API = "https://api.modrinth.com/v2"
GITHUB_API = "https://api.github.com"
DEEPSEEK_API = "https://api.deepseek.com"

DEFAULT_AI_MODEL = "deepseek-chat"
DEFAULT_AI_ENDPOINT = DEEPSEEK_API + "/chat/completions"

# Optional: "owner/repo" of the GitHub repository that publishes releases.
# When empty the launcher shows how to configure it instead of polling.
DEFAULT_UPDATE_REPO = ""
