on run argv
    try
        if (count of argv) is 0 then
            set selection to choose folder
        else
            set selection to choose folder default location (POSIX file (item 1 of argv) as alias)
        end if
        return "selected:" & POSIX path of selection
    on error number -128
        return "cancelled"
    end try
end run
