# NixOS module for the EchoBooks sync server.
#
# Imported from the flake as `nixosModules.default`. It provisions the systemd
# service (running the Nix-built `echobooks-server` directly — no runtime
# `uv sync`, no nix-ld) and, optionally, the Postgres role + database the server
# talks to over a unix socket with peer auth.
#
# Secrets (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / JWT_SECRET) are NOT in the
# Nix store — they live in an EnvironmentFile on disk (default
# /var/lib/echobooks/.env), exactly as the current deploy expects.
self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.echobooks;
  # The flake's package for this system (the env carrying `echobooks-server`).
  defaultPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
in
{
  options.services.echobooks = {
    enable = lib.mkEnableOption "the EchoBooks sync server";

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "echobooks.packages.\${system}.default";
      description = "The EchoBooks package providing the `echobooks-server` entry point.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "echobooks";
      description = "System user the service runs as (and the Postgres role name).";
    };

    stateDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/echobooks";
      description = "Working directory; also the default location of the secrets .env.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Bind address. Keep localhost-only behind a TLS-terminating proxy.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8000;
      description = "Bind port.";
    };

    database = lib.mkOption {
      type = lib.types.str;
      default = "echobooks";
      description = "Postgres database name.";
    };

    databaseUrl = lib.mkOption {
      type = lib.types.str;
      default = "postgresql+psycopg://${cfg.user}@/${cfg.database}?host=/run/postgresql";
      defaultText = lib.literalExpression
        "\"postgresql+psycopg://\${user}@/\${database}?host=/run/postgresql\"";
      description = "SQLAlchemy DSN. Defaults to a unix-socket + peer-auth connection.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = "${cfg.stateDir}/.env";
      defaultText = lib.literalExpression "\"\${stateDir}/.env\"";
      description = ''
        Path to a file with the server's secrets, sourced by systemd:
          GOOGLE_CLIENT_ID=...
          GOOGLE_CLIENT_SECRET=...
          JWT_SECRET=...
        Kept out of the Nix store. Set to null to provide them another way.
      '';
    };

    provisionDatabase = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Whether this module also configures PostgreSQL with the role + database
        (unix-socket peer auth). Disable if Postgres is managed elsewhere.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    users.users.${cfg.user} = {
      isSystemUser = true;
      group = cfg.user;
      home = cfg.stateDir;
      createHome = true;
    };
    users.groups.${cfg.user} = { };

    # --- PostgreSQL (optional) ------------------------------------------- #
    services.postgresql = lib.mkIf cfg.provisionDatabase {
      enable = lib.mkDefault true;
      ensureDatabases = [ cfg.database ];
      ensureUsers = [
        {
          name = cfg.user;
          # Peer auth over the socket → the role owns its database, no password.
          ensureDBOwnership = true;
        }
      ];
    };

    # --- The server service ---------------------------------------------- #
    systemd.services.echobooks = {
      description = "EchoBooks sync server";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ] ++ lib.optional cfg.provisionDatabase "postgresql.service";
      requires = lib.optional cfg.provisionDatabase "postgresql.service";

      environment = {
        HOST = cfg.host;
        PORT = toString cfg.port;
        DATABASE_URL = cfg.databaseUrl;
      };

      serviceConfig = {
        ExecStart = "${cfg.package}/bin/echobooks-server";
        User = cfg.user;
        Group = cfg.user;
        WorkingDirectory = cfg.stateDir;
        StateDirectory = "echobooks";
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
        Restart = "on-failure";
        RestartSec = 5;

        # Hardening — this host is internet-facing (behind Caddy/Cloudflare).
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
        RestrictNamespaces = true;
        LockPersonality = true;
        MemoryDenyWriteExecute = false; # CPython JITs/uvloop need W^X off
        ReadWritePaths = [ cfg.stateDir ];
      };
    };
  };
}
