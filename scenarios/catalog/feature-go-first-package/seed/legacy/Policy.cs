using System;

class Policy
{
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
