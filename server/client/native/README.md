<!--
  Copyright (C) Dittobuffer Contributors
  Copyright (C) chucny
  Copyright (C) gekkedavi

  This file is part of Dittobuffer. It is distributed under the GNU General
  Public License v3.0 (see LICENSE). The copyright notices in this file must
  be preserved in all copies, forks, and derivative works. Removing or
  altering these notices violates the license and copyright law.
-->

Dittobuffer

Heavily vibecoded libNianticLabsPlugin.so reimplementation allowing *easy* server development for the latest version of Pokemon GO as of this commit (0.429.2) currently the server endpoint is hardcoded inside `src/server.rs`

# usage

write a server

change the endpoint

compile

replace the library inside the pokemon go apk 

this can either be done by modifying the apk 

or by bind mounting  over the original library on a rooted device
