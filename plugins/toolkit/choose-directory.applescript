on requestFolder(argv)
    if (count of argv) is 0 then
        return choose folder
    end if
    return choose folder default location (POSIX file (item 1 of argv) as alias)
end requestFolder

on run argv
    try
        set chosenFolder to my requestFolder(argv)
        return "selected:" & POSIX path of chosenFolder
    on error number -128
        return "cancelled"
    end try
end run
