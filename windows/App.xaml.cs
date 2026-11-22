using System;
using System.Windows;
using System.Windows.Threading;

namespace MusicControl;

public partial class App : Application
{
    public App()
    {
        // Если приложение не открывается — покажем причину, а не молча закроемся.
        DispatcherUnhandledException += OnDispatcherError;
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
            ShowError(args.ExceptionObject as Exception);
    }

    private void OnDispatcherError(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        ShowError(e.Exception);
        e.Handled = true;
    }

    private static void ShowError(Exception ex)
    {
        var text = ex == null
            ? "неизвестная ошибка"
            : ex.GetType().Name + ": " + ex.Message + Environment.NewLine + Environment.NewLine + ex.StackTrace;
        MessageBox.Show(text, "Домашняя музыка — ошибка", MessageBoxButton.OK, MessageBoxImage.Error);
    }
}
