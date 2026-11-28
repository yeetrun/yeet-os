# Copyright (c) 2025 AUTHORS All rights reserved.
# Use of this source code is governed by a BSD-style
# license that can be found in the LICENSE file.

# Loaded on demand by bash-completion.
_yeet()
{
    local cur prev cword
    local -a words
    _init_completion -n '=:' || return
    mapfile -t COMPREPLY < <(command yeet __complete "${words[@]:1:cword-1}" "$cur" 2>/dev/null)
}
complete -o default -F _yeet yeet
