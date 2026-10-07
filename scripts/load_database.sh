# Runs on the benchgap.net server (the update workflow sends it over SSH):
# loads the gzipped MySQL script on stdin (`benchgap sql`) into the site's
# database. The database login is the one the site uses, read from its
# config.php, so it is never stored anywhere else.
set -euo pipefail
umask 077
login=$(mktemp)
trap 'rm -f "$login"' EXIT
php8.5 -r '
    $c = require "/web/benchgap.net/config.php";
    parse_str(str_replace(";", "&", substr($c["dsn"], strpos($c["dsn"], ":") + 1)), $d);
    $q = fn ($v) => "\"" . addcslashes((string) $v, "\\\"") . "\"";
    echo "[client]\n";
    foreach (["host" => $d["host"] ?? "localhost", "port" => $d["port"] ?? 3306, "user" => $c["username"], "password" => $c["password"], "database" => $d["dbname"]] as $k => $v) {
        echo "$k=", $q($v), "\n";
    }
' > "$login"
if [ "${1:-}" = "--check" ]; then
    mysql --defaults-extra-file="$login" -e "SELECT table_name, table_rows FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name"
else
    gunzip | mysql --defaults-extra-file="$login" --default-character-set=utf8mb4
fi
