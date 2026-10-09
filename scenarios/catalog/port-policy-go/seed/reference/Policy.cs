using System;

class Policy
{
    // Retention days for a top-level domain. Exact match, case-sensitive.
    public static int RetentionDays(string tld)
    {
        if (tld == "de") return 7;
        if (tld == "exception") return 1;
        return 30;
    }

    static void Main(string[] args)
    {
        Console.WriteLine(RetentionDays(args.Length > 0 ? args[0] : ""));
    }
}
