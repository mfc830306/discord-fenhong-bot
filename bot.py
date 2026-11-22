
                try:

                    bot.add_view(
                        RecordView(
                            record_id
                        ),
                        message_id=message_id
                    )

                except Exception as e:

                    print(
                        '恢復按鈕失敗：',
                        record_id,
                        repr(e)
                    )

            views_restored = True

            print('已還原舊分贓卡按鈕；不強制更新無法存取的舊訊息。')

        print(
            '================================'
        )

        print(
            f'Bot 已登入：'
            f'{bot.user}'
        )

        print(
            f'Slash 指令同步完成：'
            f'{len(synced)} 個'
        )

        print(
            f'管理身分組：'
            f'{MANAGER_ROLE_NAME}'
        )

        for guild in bot.guilds:

            print(
                f'伺服器：'
                f'{guild.name}'
            )

            print(
                f'目前快取成員：'
                f'{len(guild.members)} 人'
            )

            print(
                f'Discord 顯示成員數：'
                f'{guild.member_count}'
            )

            for voice_channel in (
                guild.voice_channels
            ):

                print(
                    f'語音頻道：'
                    f'{voice_channel.name}'
                    f'｜目前 '
                    f'{len(voice_channel.members)} 人'
                )

        print(
            '新增寶物：'
            '最多2筆，名稱與金額分開'
        )

        print(
            '修改市價：'
            '處理過的寶物會重新計算'
        )

        print(
            '修改市價：'
            '原領取紀錄會全部重置'
        )

        print(
            '寶物狀態：'
            '🟨 = 尚有人未領'
        )

        print(
            '寶物狀態：'
            '✅ = 全部領完'
        )

        print(
            '================================'
        )

    except Exception as e:

        print(
            '啟動錯誤：',
            repr(e)
        )


@bot.tree.error
async def log_slash_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    import traceback
    print(f'Slash 指令執行失敗：{interaction.command}，使用者：{interaction.user}', flush=True)
    traceback.print_exception(type(error), error, error.__traceback__)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message('指令執行失敗，請管理員查看 Render Logs。', ephemeral=True)
        else:
            await interaction.followup.send('指令執行失敗，請管理員查看 Render Logs。', ephemeral=True)
    except discord.HTTPException:
        pass


if not TOKEN:
    raise RuntimeError('尚未設定 DISCORD_TOKEN；請到 Render 的 Environment 設定。')

init_db()
init_boss_poll_tables()
start_render_health_server()

bot.run(TOKEN)
